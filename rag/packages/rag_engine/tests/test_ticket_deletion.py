"""#367: 削除の文を操作の指示として判定する。"""

import pytest

from rag_engine.generation.operation_audit import is_operation_instruction


@pytest.mark.parametrize("text", ["履歴を削除します。", "履歴を消去してください。", "一括削除できます。", "Delete the record."])
def test_deletion_is_an_operation(text):
    assert is_operation_instruction(text)
