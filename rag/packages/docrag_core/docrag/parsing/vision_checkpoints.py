"""Visionの応答・後処理結果と、Picture/Table別の途中状態を保存する。"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path

from docrag.parsing.checkpoints import atomic_json, digest_json, file_digest

# prompt以外の後処理・画像入力契約を変更した場合も版を上げて再利用を無効化する。
VISION_CHECKPOINT_VERSION = 1


class VisionCheckpoints:
    """同一runの再開で入力が一致する応答を再利用する。呼出し元が排他を保持する。"""

    def __init__(self, directory: Path, source_hash: str):
        self.directory = directory
        self.source_hash = source_hash
        self.entries: dict[str, dict] = {}
        self.phase = 'pictures'
        self.discovery_complete = False
        self.current = None

    def register(self, records, state='pending') -> None:
        """検出済み対象を登録する。TableはPicture処理後に追加検出される。"""
        for record in records:
            self.entries[record.id] = {'page': record.page, 'kind': record.category,
                                       'state': state, 'reused': False}

    def update(self, record=None, state=None, *, reused=False) -> None:
        """状態をatomic保存する。完了は応答・結果の永続化後にのみ通知する。"""
        if record is not None:
            self.current = {'record_id': record.id, 'page': record.page, 'kind': record.category}
            self.entries[record.id].update(state=state, reused=reused)
        counts = {name: sum(e['state'] == name for e in self.entries.values())
                  for name in ('pending', 'running', 'succeeded', 'failed', 'skipped')}
        pages = {}
        for entry in self.entries.values():
            bucket = pages.setdefault(str(entry['page']), {})
            key = entry['kind'] + ':' + entry['state']
            bucket[key] = bucket.get(key, 0) + 1
        atomic_json(self.directory / 'progress.json', {
            'schema_version': 1, 'phase': self.phase, 'total_known': len(self.entries),
            'total_final': self.discovery_complete, 'counts': counts, 'pages': pages,
            'current': self.current, 'items': self.entries,
            'updated_at': datetime.now(timezone.utc).isoformat(),
        })
        print(f'[vision-progress] phase={self.phase} current={self.current} '
              f'succeeded={counts["succeeded"]} failed={counts["failed"]} '
              f'known={len(self.entries)} total_final={self.discovery_complete}', flush=True)

    def key(self, *, crop: Path, context: Path, prompt: str, system_prompt: str,
            provider, max_tokens: int, kind: str, api_mode: str) -> str:
        """認証値を除き、入力画像・実prompt・接続先・model・出力上限をhash化する。"""
        return digest_json({'version': VISION_CHECKPOINT_VERSION, 'source': self.source_hash,
                            'images': [file_digest(crop), file_digest(context)],
                            'prompt': prompt, 'system': system_prompt, 'kind': kind,
                            'provider': provider.provider_id, 'model': provider.model,
                            'api_mode': api_mode, 'region': provider.region,
                            'endpoint': provider.base_url, 'project': provider.project_id,
                            'max_tokens': max_tokens, 'temperature': 0, 'seed': 42})

    def load(self, key: str) -> dict | None:
        """破損・失敗・入力違いはcache missとする。後処理失敗後の有効応答は再利用する。"""
        try:
            saved = json.loads((self.directory / 'checkpoints' / (key + '.json')).read_text(encoding='utf-8'))
            response = saved['response']
            if (saved.get('schema_version') == 1 and saved['key'] == key and saved.get('reusable') and isinstance(response, dict)
                    and saved['response_sha256'] == digest_json(response)):
                return response
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def save(self, key: str, response: dict | None, record, *, state: str,
             reusable: bool = True) -> None:
        """API応答を先に保存し、後処理終了時は完成recordと状態を追記する。"""
        atomic_json(self.directory / 'checkpoints' / (key + '.json'), {
            'schema_version': 1, 'key': key, 'response': response,
            'response_sha256': digest_json(response), 'reusable': reusable,
            'state': state, 'record': asdict(record),
            'updated_at': datetime.now(timezone.utc).isoformat(),
        })
