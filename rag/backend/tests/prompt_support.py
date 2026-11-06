"""保存指示が未編集であることを明示する単体テストの代役。"""


class UneditedPromptOracle:
    async def answer_prompt_overrides(self) -> dict[str, str]:
        return {}
