import ctypes as C
import queue
import unittest
from unittest.mock import Mock, patch

from hotkeys import HotkeyMatcher
from priority_hotkeys import PriorityHotkeys, KBDLLHOOKSTRUCT
from win_native import Native


def bindings(read="F2", stop="", pause=""):
    return dict(read=read, stop=stop, pause=pause)


class MatcherTests(unittest.TestCase):
    def test_exact_chord_repeat_and_balanced_release(self):
        match = HotkeyMatcher(bindings())
        self.assertEqual(match.process(0x71, True), (True, "read"))
        self.assertEqual(match.process(0x71, True), (True, None))
        self.assertEqual(match.process(0x71, False), (True, None))
        self.assertEqual(match.process(0x71, True), (True, "read"))

    def test_extra_modifiers_and_releasing_modifiers_while_held_do_not_trigger(self):
        match = HotkeyMatcher(bindings())
        match.process(0xA0, True)
        self.assertEqual(match.process(0x71, True), (False, None))
        match.process(0xA0, False)
        self.assertEqual(match.process(0x71, True), (False, None))
        match.process(0x71, False)
        self.assertEqual(match.process(0x71, True), (True, "read"))

    def test_both_modifier_sides_and_release_after_modifier_change(self):
        match = HotkeyMatcher(bindings("Ctrl+F2"), [0xA3])
        match.process(0xA2, True)
        match.process(0xA2, False)
        self.assertEqual(match.process(0x71, True), (True, "read"))
        match.process(0xA3, False)
        self.assertEqual(match.process(0x71, True), (True, None))
        self.assertEqual(match.process(0x71, False), (True, None))

    def test_empty_bindings_and_ordinary_typing_are_not_retained(self):
        match = HotkeyMatcher(bindings(read=""))
        for vk in (0x41, 0x42, 0x71):
            self.assertEqual(match.process(vk, True), (False, None))
        self.assertFalse(match.intercepted)
        self.assertFalse(match.down)


class CallbackTests(unittest.TestCase):
    def test_callback_queues_only_matching_actions_and_passes_other_input(self):
        events = queue.Queue()
        with patch("priority_hotkeys.U.GetAsyncKeyState", return_value=0):
            hook = PriorityHotkeys(events, bindings())
        key = KBDLLHOOKSTRUCT(vkCode=0x71)
        pointer = C.addressof(key)
        with patch("priority_hotkeys.U.CallNextHookEx", return_value=17) as next_hook:
            self.assertEqual(hook._callback(-1, 0x100, pointer), 17)
            self.assertEqual(hook._callback(0, 0x100, pointer), 1)
            self.assertEqual(hook._callback(0, 0x100, pointer), 1)
            self.assertEqual(hook._callback(0, 0x101, pointer), 1)
            key.vkCode = 0x41
            self.assertEqual(hook._callback(0, 0x100, pointer), 17)
            self.assertEqual(next_hook.call_count, 2)
        self.assertEqual(events.get_nowait()[0], "read_selection")
        self.assertTrue(events.empty())

    def test_callback_exception_disables_interception_and_reports_once(self):
        events = queue.Queue()
        with patch("priority_hotkeys.U.GetAsyncKeyState", return_value=0):
            hook = PriorityHotkeys(events, bindings())
        key = KBDLLHOOKSTRUCT(vkCode=0x71)
        with patch.object(hook.matcher, "process", side_effect=RuntimeError), \
             patch("priority_hotkeys.U.CallNextHookEx", return_value=17):
            self.assertEqual(hook._callback(0, 0x100, C.addressof(key)), 17)
            self.assertEqual(hook._callback(0, 0x100, C.addressof(key)), 17)
        self.assertFalse(hook.enabled)
        self.assertEqual(events.get_nowait()[0], "error")
        self.assertTrue(events.empty())


class NativeModeTests(unittest.TestCase):
    def native(self, priority=False):
        native = Native.__new__(Native)
        native.events = queue.Queue()
        native.hwnd = 101
        native.bindings = {"read": "F4"}
        native.priority = priority
        native.priority_hook = Mock() if priority else None
        native.registered = set() if priority else {1}
        native._tray = Mock()
        return native

    def test_priority_mode_does_not_register_occupied_hotkey(self):
        native = self.native()
        with patch("win_native.U.UnregisterHotKey"), \
             patch("win_native.U.RegisterHotKey", return_value=False) as register, \
             patch("win_native.PriorityHotkeys") as hook:
            native._keys(bindings(), True)
        register.assert_not_called()
        hook.return_value.install.assert_called_once()
        event, result = native.events.get_nowait()
        self.assertTrue(result["success"] and result["priority"])
        self.assertEqual(result["bindings"], bindings())

    def test_install_failure_restores_previous_normal_registration(self):
        native = self.native()
        with patch("win_native.U.UnregisterHotKey"), \
             patch("win_native.U.RegisterHotKey", return_value=True), \
             patch("win_native.PriorityHotkeys") as hook:
            hook.return_value.install.side_effect = C.WinError(5)
            native._keys(bindings(), True)
        result = native.events.get_nowait()[1]
        self.assertFalse(result["success"] or result["priority"])
        self.assertEqual(result["bindings"]["read"], "F4")

    def test_disabling_priority_while_conflicting_restores_priority_mode(self):
        native = self.native(priority=True)
        previous = native.priority_hook
        with patch("win_native.U.RegisterHotKey", return_value=False), \
             patch("win_native.PriorityHotkeys") as hook:
            native._keys(bindings(), False)
        previous.close.assert_called_once()
        hook.return_value.install.assert_called_once()
        result = native.events.get_nowait()[1]
        self.assertFalse(result["success"])
        self.assertTrue(result["priority"])
        self.assertEqual(result["bindings"]["read"], "F4")

    def test_all_empty_bindings_close_hook_and_install_nothing(self):
        native = self.native(priority=True)
        previous = native.priority_hook
        with patch("win_native.PriorityHotkeys") as hook:
            native._keys(bindings(read=""), True)
        previous.close.assert_called_once()
        hook.assert_not_called()
        self.assertTrue(native.events.get_nowait()[1]["success"])


if __name__ == "__main__":
    unittest.main()
