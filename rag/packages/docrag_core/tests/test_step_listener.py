"""工程の開始・終了の通知(進捗の表示。#593)を検証する。"""

import unittest

from docrag.generation.execution_record import _execution_step, bind_step_listener


class StepListenerTests(unittest.TestCase):
    def test_listener_receives_nested_steps_in_order(self):
        events = []
        with bind_step_listener(lambda name, outcome, elapsed: events.append((name, outcome))):
            with _execution_step("回答生成フロー"):
                with _execution_step("文書検索"):
                    pass
        self.assertEqual(
            events,
            [
                ("回答生成フロー", "started"),
                ("文書検索", "started"),
                ("文書検索", "success"),
                ("回答生成フロー", "success"),
            ],
        )

    def test_failed_step_is_reported_as_error_and_still_raises(self):
        events = []
        with bind_step_listener(lambda name, outcome, elapsed: events.append((name, outcome))):
            with self.assertRaises(ValueError):
                with _execution_step("文書検索"):
                    raise ValueError("boom")
        self.assertEqual(events, [("文書検索", "started"), ("文書検索", "error")])

    def test_listener_failure_does_not_stop_the_step(self):
        def broken(name, outcome, elapsed):
            raise RuntimeError("listener down")

        ran = []
        with bind_step_listener(broken):
            with _execution_step("文書検索"):
                ran.append(True)
        self.assertEqual(ran, [True])

    def test_no_listener_outside_binding(self):
        events = []
        with bind_step_listener(lambda name, outcome, elapsed: events.append(name)):
            pass
        with _execution_step("文書検索"):
            pass
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
