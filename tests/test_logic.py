import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from finance_bot import attachments as att
from finance_bot import inbox
from finance_bot.config import ConfigError, load_config, load_dotenv, parse_dotenv
from finance_bot.conversation import Conversation, ConversationStore, SIZE_NOTICE
from finance_bot.messaging import should_respond, split_message, strip_mention
from finance_bot.prompt import load_sheet_reference
from finance_bot.tools.sheet import SheetError, build_sheet_argv
from finance_bot.tools import skills as store


def make_repo(root: Path) -> None:
    (root / "spreadsheet.ods").write_text("x")
    (root / "claude-sheets" / "src").mkdir(parents=True)
    (root / "claude-sheets" / "src" / "sheet.py").write_text("")
    (root / "skills").mkdir()


ENV = {"DISCORD_TOKEN": "t", "ANTHROPIC_API_KEY": "k", "ALLOWED_USER_IDS": "1, 2"}


class DotenvTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / ".env"

    def load(self, text, env=None):
        self.path.write_text(text)
        env = {} if env is None else env
        load_dotenv(env, self.path)
        return env

    def test_comments_blanks_quotes_export(self):
        env = self.load("# c\n\nA=1\nexport B=two\nC=\"x y\"\nD='z'\nE=\nF=a=b\n  G = 7 \n")
        self.assertEqual(env, {"A": "1", "B": "two", "C": "x y", "D": "z", "E": "", "F": "a=b", "G": "7"})

    def test_existing_env_wins(self):
        env = self.load("A=file\nB=file\n", {"A": "real"})
        self.assertEqual(env, {"A": "real", "B": "file"})

    def test_missing_file(self):
        env = {"A": "1"}
        load_dotenv(env, self.path)
        self.assertEqual(env, {"A": "1"})

    def test_malformed_line_reports_location(self):
        for bad in ("A=1\nnoequals\n", "A=1\n=v\n", "A=1\nbad key=v\n"):
            with self.assertRaises(ConfigError) as cm:
                parse_dotenv(bad, "f.env")
            self.assertIn("f.env:2", str(cm.exception))

    def test_malformed_error_omits_value(self):
        with self.assertRaises(ConfigError) as cm:
            parse_dotenv("secret value\n", "f.env")
        self.assertNotIn("secret", str(cm.exception))


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        make_repo(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_ok(self):
        cfg = load_config({**ENV, "CHANNEL_IDS": "5"}, self.root)
        self.assertEqual(cfg.allowed_user_ids, {1, 2})
        self.assertEqual(cfg.channel_ids, {5})
        self.assertEqual(cfg.effort, "medium")
        self.assertEqual(cfg.sheet_python, "/usr/bin/python3")
        self.assertEqual(cfg.history_idle_s, 1800)
        self.assertEqual(load_config({**ENV, "HISTORY_IDLE_MINUTES": "5"}, self.root).history_idle_s, 300)

    def test_missing(self):
        with self.assertRaisesRegex(ConfigError, "DISCORD_TOKEN"):
            load_config({"ALLOWED_USER_IDS": "1", "ANTHROPIC_API_KEY": "k"}, self.root)

    def test_bad_values(self):
        for bad in ({"ALLOWED_USER_IDS": "a"}, {"EFFORT": "huge"}, {"SPREADSHEET_PATH": "nope.ods"}, {"HISTORY_IDLE_MINUTES": "0"}, {"HISTORY_MAX_CHARS": "x"}):
            with self.assertRaises(ConfigError):
                load_config({**ENV, **bad}, self.root)


class ConversationTests(unittest.TestCase):
    def test_append_and_rollback_truncate_tail_only(self):
        c = Conversation([{"type": "text", "text": "sys"}])
        c.append({"role": "user", "content": "one"})
        c.append({"role": "assistant", "content": [{"type": "text", "text": "a"}]})
        mark = c.mark()
        c.append({"role": "user", "content": "two"})
        before = c.size_chars()
        c.rollback_to(mark)
        self.assertEqual([m["content"] for m in c.messages][0], "one")
        self.assertEqual(len(c.messages), 2)
        self.assertLess(c.size_chars(), before)

    def test_idle_wipe_and_keep(self):
        now = [0.0]
        store = ConversationStore(idle_s=1800, max_chars=10**6, clock=lambda: now[0])
        c1, n1 = store.acquire(1, lambda: ["sys1"])
        c1.append({"role": "user", "content": "x"})
        now[0] = 1700
        c2, _ = store.acquire(1, lambda: ["sys2"])
        self.assertIs(c1, c2)
        self.assertEqual(c2.system, ["sys1"])
        now[0] = 1700 + 1801
        c3, n3 = store.acquire(1, lambda: ["sys3"])
        self.assertIsNot(c1, c3)
        self.assertEqual(c3.messages, [])
        self.assertIsNone(n3)

    def test_touch_extends_idle_window(self):
        now = [0.0]
        store = ConversationStore(idle_s=100, max_chars=10**6, clock=lambda: now[0])
        c, _ = store.acquire(1, lambda: [])
        now[0] = 90
        store.touch(c)
        now[0] = 180
        self.assertIs(store.acquire(1, lambda: [])[0], c)

    def test_size_ceiling_wipes_with_notice(self):
        store = ConversationStore(idle_s=1800, max_chars=10, clock=lambda: 0.0)
        c, _ = store.acquire(1, lambda: [])
        c.append({"role": "user", "content": "x" * 11})
        c2, notice = store.acquire(1, lambda: [])
        self.assertIsNot(c, c2)
        self.assertEqual(notice, SIZE_NOTICE)

    def test_reset_and_channels_independent(self):
        store = ConversationStore(1800, 10**6, clock=lambda: 0.0)
        a, _ = store.acquire(1, lambda: [])
        b, _ = store.acquire(2, lambda: [])
        store.reset(1)
        self.assertIsNot(store.acquire(1, lambda: [])[0], a)
        self.assertIs(store.acquire(2, lambda: [])[0], b)


class AttachmentTests(unittest.TestCase):
    def test_classify_case_insensitive(self):
        self.assertEqual([att.classify(n) for n in ("a.CSV", "b.Pdf", "c.JPEG", "d.xlsx", "e.exe", "f")], ["text", "pdf", "image", "spreadsheet", None, None])

    def test_rejection(self):
        self.assertIn("unsupported", att.rejection("a.exe", 1))
        self.assertIn("25 MB", att.rejection("a.csv", 25 * att.MB + 1))
        self.assertIsNone(att.rejection("a.csv", 25 * att.MB))

    def test_sanitize(self):
        self.assertEqual(att.sanitize_filename("../../etc/pa ss\\wd.csv"), "wd.csv")
        self.assertEqual(att.sanitize_filename("my stmt (1).csv"), "my_stmt__1_.csv")
        self.assertEqual(att.sanitize_filename("..."), "file")
        self.assertEqual(att.sanitize_filename(".hidden"), "hidden")

    def test_unique_name(self):
        self.assertEqual(att.unique_name("a.csv", set()), "a.csv")
        self.assertEqual(att.unique_name("a.csv", {"a.csv", "a-1.csv"}), "a-2.csv")

    def test_text_inline_and_truncation(self):
        parts = att.saved_parts("a.csv", "inbox/1/a.csv", "text", b"x,y\n1,2")
        self.assertEqual(parts[0][0]["text"], "Attachment: a.csv saved at inbox/1/a.csv (7 B, text, inlined)")
        self.assertIn("x,y\n1,2", parts[1][0]["text"])
        big = att.saved_parts("a.txt", "inbox/1/a.txt", "text", b"z" * 20001)
        self.assertIn("truncated", big[0][0]["text"])
        self.assertIn("first 20000 of 20001", big[1][0]["text"])
        self.assertEqual(big[1][0]["text"].count("z"), 20000)

    def test_text_invalid_utf8_and_empty(self):
        parts = att.saved_parts("a.txt", "p", "text", b"\xff")
        self.assertIn("\ufffd", parts[1][0]["text"])
        self.assertEqual(att.saved_parts("a.txt", "p", "text", b"")[1][0]["text"], "(empty file)")

    def test_image_inline_and_oversize(self):
        parts = att.saved_parts("a.JPG", "p", "image", b"abc")
        block, est = parts[1]
        self.assertEqual(block["source"]["media_type"], "image/jpeg")
        self.assertEqual(block["source"]["data"], "YWJj")
        self.assertEqual(est, att.IMAGE_EST_CHARS)
        self.assertEqual(len(att.saved_parts("a.png", "p", "image", b"x" * (att.MAX_IMAGE_INLINE_BYTES + 1))), 1)

    def test_message_inline_budget(self):
        data = b"x" * (3 * att.MB)
        self.assertEqual(len(att.saved_parts("a.pdf", "p", "pdf", data, budget=3 * att.MB)), 2)
        over = att.saved_parts("a.pdf", "p", "pdf", data, budget=3 * att.MB - 1)
        self.assertEqual(len(over), 1)
        self.assertIn("saved only (message inline limit reached)", over[0][0]["text"])
        self.assertTrue(att.binary_inlined(att.saved_parts("a.png", "p", "image", b"x")))
        self.assertFalse(att.binary_inlined(att.saved_parts("a.csv", "p", "text", b"x", budget=0)))
        self.assertEqual(len(att.saved_parts("a.csv", "p", "text", b"x", budget=0)), 2)

    def test_pdf_pages_and_inline_limit(self):
        data = b"/Type /Pages /Type/Page> /Type /Page\n/Contents"
        self.assertEqual(att.pdf_pages(data), 2)
        self.assertEqual(att.pdf_pages(b"nothing"), 1)
        block, est = att.saved_parts("a.pdf", "p", "pdf", data)[1]
        self.assertEqual((block["type"], est), ("document", 2 * att.PDF_PAGE_EST_CHARS))
        self.assertEqual(len(att.saved_parts("a.pdf", "p", "pdf", b"x" * (att.MAX_PDF_INLINE_BYTES + 1))), 1)

    def test_spreadsheet_save_only_and_rejected_note(self):
        parts = att.saved_parts("a.ods", "p", "spreadsheet", b"1")
        self.assertEqual(len(parts), 1)
        self.assertIn("saved only", parts[0][0]["text"])
        self.assertIn("not saved: nope", att.rejected_parts("a.exe", "nope")[0][0]["text"])

    def test_build_turn_empty_text_and_size_excludes_base64(self):
        parts = att.saved_parts("a.png", "p", "image", b"x" * 100000)
        turn = att.build_turn("", parts)
        self.assertEqual(turn.blocks[0], {"type": "text", "text": att.NO_TEXT_PLACEHOLDER})
        self.assertEqual(turn.size_chars, len(att.NO_TEXT_PLACEHOLDER) + len(parts[0][0]["text"]) + att.IMAGE_EST_CHARS)
        self.assertEqual({k for b in turn.blocks for k in b}, {"type", "text", "source"})

    def test_size_override_prevents_base64_wipe(self):
        store = ConversationStore(1800, 10**6, clock=lambda: 0.0)
        conv, _ = store.acquire(1, lambda: [])
        turn = att.build_turn("hi", att.saved_parts("a.png", "p", "image", b"x" * 4 * att.MB))
        conv.append({"role": "user", "content": turn.blocks}, turn.size_chars)
        self.assertLess(conv.size_chars(), 10**5)
        self.assertIs(store.acquire(1, lambda: [])[0], conv)


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_relative_path_and_collision(self):
        self.assertEqual(inbox.save(self.root, 7, "../a b.csv", b"1"), "inbox/7/a_b.csv")
        self.assertEqual(inbox.save(self.root, 7, "a b.csv", b"2"), "inbox/7/a_b-1.csv")
        self.assertEqual((self.root / "inbox/7/a_b.csv").read_bytes(), b"1")

    def test_wipe_hooks(self):
        wiped = []
        store = ConversationStore(100, 10, clock=lambda: now[0], on_wipe=wiped.append)
        now = [0.0]
        c, _ = store.acquire(1, lambda: [])
        self.assertEqual(wiped, [])
        c.append({"role": "user", "content": "x" * 11})
        store.acquire(1, lambda: [])
        self.assertEqual(wiped, [1])
        now[0] = 500
        store.acquire(1, lambda: [])
        self.assertEqual(wiped, [1, 1])
        store.reset(1)
        self.assertEqual(wiped, [1, 1, 1])

    def test_wipe_channel_and_all(self):
        inbox.save(self.root, 1, "a.csv", b"1")
        inbox.save(self.root, 2, "a.csv", b"1")
        inbox.wipe_channel(self.root, 1)
        self.assertFalse((self.root / "inbox/1").exists())
        self.assertTrue((self.root / "inbox/2/a.csv").exists())
        inbox.wipe_all(self.root)
        self.assertFalse((self.root / "inbox").exists())


class MessagingTests(unittest.TestCase):
    def test_split(self):
        text = ("word " * 30 + "\n") * 40
        chunks = split_message(text, 200)
        self.assertTrue(all(len(c) <= 200 for c in chunks))
        self.assertEqual("".join(chunks).split(), text.split())

    def test_split_no_break_points_and_short(self):
        self.assertEqual(split_message("x" * 450, 200), ["x" * 200, "x" * 200, "x" * 50])
        self.assertEqual(split_message("hi"), ["hi"])
        self.assertEqual(split_message(""), [])

    def test_strip_mention(self):
        self.assertEqual(strip_mention("<@9> hello <@!9>", 9), "hello")

    def test_should_respond(self):
        allowed, chans = frozenset({1}), frozenset({50})
        self.assertFalse(should_respond(2, allowed, True, True, 50, chans))
        self.assertTrue(should_respond(1, allowed, True, False, 7, chans))
        self.assertTrue(should_respond(1, allowed, False, True, 7, chans))
        self.assertTrue(should_respond(1, allowed, False, False, 50, chans))
        self.assertFalse(should_respond(1, allowed, False, False, 7, chans))


class SheetArgvTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        make_repo(Path(self.tmp.name))
        self.cfg = load_config(ENV, Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_argv_forces_file(self):
        argv = build_sheet_argv(self.cfg, "read", ["--sheet", "A", "--range", "A1:B2"])
        self.assertEqual(argv[0], "/usr/bin/python3")
        self.assertEqual(argv[2:4], ["read", str(self.cfg.spreadsheet_path)])
        self.assertEqual(argv[4:], ["--sheet", "A", "--range", "A1:B2"])

    def test_rejects(self):
        for action, args in (("stop", []), ("create", []), ("rm", []), ("batch", ["--ops-file", "/etc/passwd"]), ("image", ["--path=/etc/passwd"])):
            with self.assertRaises(SheetError):
                build_sheet_argv(self.cfg, action, args)


class SkillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def md(self, name="monthly-report"):
        return f"---\nname: {name}\ndescription: Monthly summary\n---\nbody\n"

    def test_names(self):
        for bad in ("../x", "A", "a/b", "", "a_b", "a.b", "x" * 65):
            with self.assertRaises(store.SkillError):
                store.skill_dir(self.root, bad)
        self.assertEqual(store.skill_dir(self.root, "ok-1").name, "ok-1")

    def test_script_filenames(self):
        for bad in ("../a.py", "a/b.py", "a.sh", ".py", "a.py.txt", "x" * 70 + ".py"):
            with self.assertRaises(store.SkillError):
                store.prepare_skill("monthly-report", self.md(), {bad: "pass"})

    def test_frontmatter_must_match_name(self):
        with self.assertRaises(store.SkillError):
            store.prepare_skill("other", self.md(), {})
        with self.assertRaises(store.SkillError):
            store.prepare_skill("a", "no frontmatter", {})

    def test_write_list_read_update_diff(self):
        skill = store.prepare_skill("monthly-report", self.md(), {"run.py": "print(1)\n"})
        self.assertFalse(store.skill_exists(self.root, "monthly-report"))
        store.write_skill(self.root, skill)
        info = store.list_skills(self.root)
        self.assertEqual([(s.name, s.description) for s in info], [("monthly-report", "Monthly summary")])
        self.assertIn("print(1)", store.read_skill(self.root, "monthly-report"))
        updated = store.prepare_skill("monthly-report", self.md(), {"run.py": "print(2)\n"})
        diff = store.skill_diff(self.root, updated)
        self.assertIn("+print(2)", diff)
        self.assertNotIn("SKILL.md", diff)

    def test_script_path_traversal(self):
        store.write_skill(self.root, store.prepare_skill("monthly-report", self.md(), {"run.py": "pass"}))
        self.assertTrue(store.script_path(self.root, "monthly-report", "run.py").is_file())
        for skill, script in (("..", "run.py"), ("monthly-report", "../run.py"), ("monthly-report", "missing.py")):
            with self.assertRaises(store.SkillError):
                store.script_path(self.root, skill, script)

    def test_symlinked_skill_rejected(self):
        outside = Path(tempfile.mkdtemp())
        (outside / "evil.py").write_text("pass")
        (self.root / "evil").symlink_to(outside)
        with self.assertRaises(store.SkillError):
            store.script_path(self.root, "evil", "evil.py")


class PromptTests(unittest.TestCase):
    def test_reference_slice_excludes_rules_and_example(self):
        md = "# Rules\n1. one command\n\n# Disambiguation\nbody\n\n# Actions\ntable\n\n# Example\nUser: x\n"
        ref = load_sheet_reference(md)
        self.assertIn("table", ref)
        self.assertNotIn("one command", ref)
        self.assertNotIn("User: x", ref)

    def test_real_skill_file_parses(self):
        path = Path(__file__).resolve().parents[1] / "claude-sheets/.claude/skills/sheet/SKILL.md"
        if path.is_file():
            self.assertIn("| read |", load_sheet_reference(path.read_text()))


try:
    import anthropic  # noqa: F401
    from finance_bot import agent
except ImportError:
    agent = None


class FakeBlock:
    def __init__(self, type, **kw):
        self.type = type
        self.__dict__.update(kw)

    def model_dump_json(self):
        return "{}"


class FakeResponse:
    model = "fake"

    def __init__(self, stop_reason, content):
        self.stop_reason = stop_reason
        self.content = content


class FakeStream:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_final_message(self):
        return self.response


class FakeClient:
    def __init__(self, items):
        self.items = list(items)
        self.beta = self
        self.messages = self

    def stream(self, **kw):
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeStream(item)


def tool_use(id, name="list_skills", input=None):
    return FakeResponse("tool_use", [FakeBlock("text", text="calling"), FakeBlock("tool_use", id=id, name=name, input=input or {})])


def end(text="done"):
    return FakeResponse("end_turn", [FakeBlock("text", text=text)])


def assert_tool_pairing(test, messages):
    for i, m in enumerate(messages):
        ids = [b.id for b in m["content"] if m["role"] == "assistant" and not isinstance(m["content"], str) and b.type == "tool_use"]
        if not ids:
            continue
        test.assertLess(i + 1, len(messages))
        results = messages[i + 1]
        test.assertEqual(results["role"], "user")
        test.assertEqual([r["tool_use_id"] for r in results["content"]], ids)


@unittest.skipIf(agent is None, "anthropic not installed")
class AgentLoopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        make_repo(Path(self.tmp.name))
        self.cfg = load_config(ENV, Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    async def approver(self, summary, files):
        return True

    async def run_turn(self, items, conv=None):
        conv = conv or Conversation([])
        result = await agent.run_turn(FakeClient(items), self.cfg, self.approver, conv, "hi")
        return result, conv

    async def test_tool_round_is_append_only_and_paired(self):
        result, conv = await self.run_turn([tool_use("t1"), end("ok")])
        self.assertTrue(result.ok)
        self.assertEqual(result.text, "ok")
        self.assertEqual([m["role"] for m in conv.messages], ["user", "assistant", "user", "assistant"])
        self.assertEqual(conv.messages[2]["content"][0]["tool_use_id"], "t1")
        assert_tool_pairing(self, conv.messages)

    async def test_parallel_tool_results_in_one_message(self):
        two = FakeResponse("tool_use", [FakeBlock("tool_use", id="a", name="list_skills", input={}), FakeBlock("tool_use", id="b", name="nope", input={})])
        _, conv = await self.run_turn([two, end()])
        results = conv.messages[2]["content"]
        self.assertEqual([r["tool_use_id"] for r in results], ["a", "b"])
        self.assertNotIn("is_error", results[0])
        self.assertTrue(results[1]["is_error"])

    async def test_block_content_with_size_override(self):
        turn = att.build_turn("", att.saved_parts("a.png", "p", "image", b"x" * 1000))
        conv = Conversation([])
        result = await agent.run_turn(FakeClient([end("ok")]), self.cfg, self.approver, conv, turn.blocks, turn.size_chars)
        self.assertTrue(result.ok)
        self.assertEqual(conv.messages[0]["content"], turn.blocks)
        self.assertEqual(conv._sizes[0], turn.size_chars)

    async def test_second_turn_extends_without_editing(self):
        _, conv = await self.run_turn([tool_use("t1"), end("one")])
        snapshot = list(conv.messages)
        await agent.run_turn(FakeClient([end("two")]), self.cfg, self.approver, conv, "again")
        self.assertEqual(conv.messages[: len(snapshot)], snapshot)
        self.assertEqual(len(conv.messages), len(snapshot) + 2)

    async def test_refusal_and_max_tokens_roll_back_whole_turn(self):
        for stop in ("refusal", "max_tokens"):
            conv = Conversation([])
            conv.append({"role": "user", "content": "earlier"})
            conv.append({"role": "assistant", "content": [FakeBlock("text", text="x")]})
            result, conv = await self.run_turn([tool_use("t1"), FakeResponse(stop, [])], conv)
            self.assertFalse(result.ok)
            self.assertEqual(len(conv.messages), 2)

    async def test_exception_rolls_back_and_propagates(self):
        conv = Conversation([])
        with self.assertRaises(RuntimeError):
            await self.run_turn([tool_use("t1"), RuntimeError("boom")], conv)
        self.assertEqual(conv.messages, [])

    async def test_iteration_cap_leaves_valid_history(self):
        original = agent.MAX_TOOL_ITERATIONS
        agent.MAX_TOOL_ITERATIONS = 2
        try:
            result, conv = await self.run_turn([tool_use("a"), tool_use("b"), end()])
        finally:
            agent.MAX_TOOL_ITERATIONS = original
        self.assertTrue(result.ok)
        self.assertEqual(conv.messages[-1]["role"], "user")
        assert_tool_pairing(self, conv.messages)


if __name__ == "__main__":
    unittest.main()
