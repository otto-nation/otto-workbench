"""Tests for otto_queue.slots: counting and holding slots, with the policy deciding room."""

import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import otto_queue.slots  # noqa: E402
from otto_queue.model import EntryClass  # noqa: E402

HEAVY, LIGHT = EntryClass.HEAVY, EntryClass.LIGHT


@dataclass
class FakePolicy:
    slots: int = 2
    room: bool = True
    seen: list = field(default_factory=list)

    def limit(self, entry_class):
        return self.slots

    def verdict(self, entry_class, running):
        self.seen.append(running)
        return SimpleNamespace(admit=self.room, reason="room" if self.room else "no room")


@dataclass
class RaisingOncePolicy:
    slots: int = 1
    raised: bool = False

    def limit(self, entry_class):
        return self.slots

    def verdict(self, entry_class, running):
        if not self.raised:
            self.raised = True
            raise RuntimeError("boom")
        return SimpleNamespace(admit=True, reason="room")


def test_no_more_than_the_limit_are_admitted(tmp_path):
    policy = FakePolicy()
    first = otto_queue.slots.try_admit(tmp_path, policy, HEAVY)
    second = otto_queue.slots.try_admit(tmp_path, policy, HEAVY)
    third = otto_queue.slots.try_admit(tmp_path, policy, HEAVY)
    assert first.admitted and second.admitted and not third.admitted
    assert third.reason == "all 2 heavy slots are taken"
    first.slot.release()
    fourth = otto_queue.slots.try_admit(tmp_path, policy, HEAVY)
    assert fourth.admitted and fourth.slot.index == first.slot.index


def test_the_policy_is_told_how_many_others_are_running(tmp_path):
    policy = FakePolicy(slots=3)
    otto_queue.slots.try_admit(tmp_path, policy, HEAVY)
    otto_queue.slots.try_admit(tmp_path, policy, HEAVY)
    assert policy.seen == [0, 1]


def test_a_refusal_gives_the_probed_slot_back(tmp_path):
    policy = FakePolicy(slots=1, room=False)
    refused = otto_queue.slots.try_admit(tmp_path, policy, HEAVY)
    assert (refused.admitted, refused.reason) == (False, "no room")
    policy.room = True
    assert otto_queue.slots.try_admit(tmp_path, policy, HEAVY).admitted


def test_classes_have_separate_slots(tmp_path):
    policy = FakePolicy(slots=1)
    assert otto_queue.slots.try_admit(tmp_path, policy, HEAVY).admitted
    assert otto_queue.slots.try_admit(tmp_path, policy, LIGHT).admitted


def test_a_policy_that_raises_gives_the_slot_back(tmp_path):
    policy = RaisingOncePolicy()
    try:
        otto_queue.slots.try_admit(tmp_path, policy, HEAVY)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError")
    assert otto_queue.slots.try_admit(tmp_path, policy, HEAVY).admitted


def test_interrupt_during_the_second_probe_gives_the_slot_back(tmp_path, monkeypatch):
    calls = {"n": 0}
    real = otto_queue.slots._probe

    def probe(root, entry_class, index):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return real(root, entry_class, index)

    monkeypatch.setattr(otto_queue.slots, "_probe", probe)
    try:
        otto_queue.slots.try_admit(tmp_path, FakePolicy(slots=2), HEAVY)
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("expected KeyboardInterrupt")
    monkeypatch.setattr(otto_queue.slots, "_probe", real)
    again = otto_queue.slots.try_admit(tmp_path, FakePolicy(slots=1), HEAVY)
    assert again.admitted
    again.slot.release()
