import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import autostop  # noqa: E402


def acf(appid, name, flags, to_download=0, downloaded=0):
    return f'''"AppState"
{{
	"appid"		"{appid}"
	"name"		"{name}"
	"StateFlags"		"{flags}"
	"BytesToDownload"		"{to_download}"
	"BytesDownloaded"		"{downloaded}"
}}
'''


class ParseVdfTest(unittest.TestCase):
    def test_nested_blocks_escapes_and_comments(self):
        text = r'''
        // komentarz
        "LibraryFolders"
        {
            "0"
            {
                "path"  "C:\\Program Files (x86)\\Steam"
                "apps" { "570" "123" }
            }
            unquoted value
        }
        '''
        data = autostop.parse_vdf(text)
        folder = data["libraryfolders"]["0"]
        self.assertEqual(folder["path"], r"C:\Program Files (x86)\Steam")
        self.assertEqual(folder["apps"], {"570": "123"})
        self.assertEqual(data["libraryfolders"]["unquoted"], "value")

    def test_truncated_file_does_not_crash(self):
        data = autostop.parse_vdf('"AppState"\n{\n"name" "Gra"\n"StateFlags"')
        self.assertEqual(data["appstate"]["name"], "Gra")
        self.assertEqual(data["appstate"]["stateflags"], "")


class SteamTest(unittest.TestCase):
    def test_fully_installed_game_is_not_pending(self):
        self.assertIsNone(autostop.parse_steam_manifest(acf(570, "Dota 2", 4)))

    def test_update_required_but_not_started_is_not_pending(self):
        self.assertIsNone(autostop.parse_steam_manifest(acf(570, "Dota 2", 6)))

    def test_downloading_game_is_pending_with_progress(self):
        game = autostop.parse_steam_manifest(acf(570, "Dota 2", 1026, 200, 50))
        self.assertEqual(game.name, "Dota 2")
        self.assertEqual(game.launcher, "Steam")
        self.assertAlmostEqual(game.progress, 0.25)
        self.assertEqual(game.label(), "Dota 2 [Steam 25%]")

    def test_paused_download_is_not_pending(self):
        self.assertIsNone(autostop.parse_steam_manifest(acf(570, "Dota 2", 1538, 200, 50)))

    def test_unknown_progress(self):
        game = autostop.parse_steam_manifest(acf(570, "Dota 2", 1026))
        self.assertIsNone(game.progress)
        self.assertEqual(game.label(), "Dota 2 [Steam]")

    def test_libraries_and_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Steam"
            second = Path(tmp) / "SteamLibrary"
            (root / "steamapps").mkdir(parents=True)
            (second / "steamapps").mkdir(parents=True)
            vdf_path = lambda p: str(p).replace("\\", "\\\\")  # noqa: E731
            (root / "steamapps" / "libraryfolders.vdf").write_text(f'''
                "libraryfolders"
                {{
                    "0" {{ "path" "{vdf_path(root)}" }}
                    "1" {{ "path" "{vdf_path(second)}" }}
                    "2" {{ "path" "{vdf_path(Path(tmp) / 'missing')}" }}
                }}''', encoding="utf-8")
            (root / "steamapps" / "appmanifest_1.acf").write_text(acf(1, "Gotowa", 4))
            (second / "steamapps" / "appmanifest_2.acf").write_text(
                acf(2, "Pobierana", 1026, 10, 5))

            libraries = autostop.steam_library_dirs(root)
            self.assertEqual(libraries, [root / "steamapps", second / "steamapps"])
            games = autostop.scan_steam(libraries)
            self.assertEqual([g.name for g in games], ["Pobierana"])

    def test_old_libraryfolders_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Steam"
            second = Path(tmp) / "Games"
            (root / "steamapps").mkdir(parents=True)
            (second / "steamapps").mkdir(parents=True)
            (root / "steamapps" / "libraryfolders.vdf").write_text(
                '"LibraryFolders"\n{\n"TimeNextStatsReport" "123"\n'
                f'"1" "{str(second).replace(chr(92), chr(92) * 2)}"\n}}\n')
            self.assertEqual(autostop.steam_library_dirs(root),
                             [root / "steamapps", second / "steamapps"])


class EpicTest(unittest.TestCase):
    def test_incomplete_install_is_pending(self):
        game = autostop.parse_epic_item(json.dumps(
            {"DisplayName": "Fortnite", "bIsIncompleteInstall": True}))
        self.assertEqual(game.label(), "Fortnite [Epic]")

    def test_complete_install_and_garbage(self):
        self.assertIsNone(autostop.parse_epic_item(json.dumps(
            {"DisplayName": "Fortnite", "bIsIncompleteInstall": False})))
        self.assertIsNone(autostop.parse_epic_item("{nie json"))
        self.assertIsNone(autostop.parse_epic_item("[]"))

    def test_scan_reads_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "abc.item"
            path.write_text(json.dumps({"DisplayName": "Gra", "bIsIncompleteInstall": True}),
                            encoding="utf-8-sig")
            self.assertEqual([g.name for g in autostop.scan_epic(Path(tmp))], ["Gra"])


class LauncherProcessTest(unittest.TestCase):
    def test_known_names(self):
        no_exe = lambda: None  # noqa: E731
        self.assertTrue(autostop.is_launcher_process("Steam.exe", no_exe, set()))
        self.assertTrue(autostop.is_launcher_process("EpicGamesLauncher.exe", no_exe, set()))
        self.assertFalse(autostop.is_launcher_process("steamwebhelper.exe", no_exe, set()))
        self.assertFalse(autostop.is_launcher_process(None, no_exe, set()))

    def test_extra_names(self):
        self.assertTrue(autostop.is_launcher_process("XboxPcApp.exe", lambda: None, {"xboxpcapp"}))

    def test_generic_name_needs_matching_path(self):
        battle_net = lambda: r"C:\ProgramData\Battle.net\Agent\Agent.9000\Agent.exe"  # noqa: E731
        other = lambda: r"C:\Program Files\Something\Agent.exe"  # noqa: E731
        self.assertTrue(autostop.is_launcher_process("Agent.exe", battle_net, set()))
        self.assertFalse(autostop.is_launcher_process("Agent.exe", other, set()))
        self.assertFalse(autostop.is_launcher_process("Agent.exe", lambda: None, set()))


class RateMeterTest(unittest.TestCase):
    def test_rate_from_counter_deltas(self):
        samples = iter([
            {1: 1000, 2: 500},
            {1: 3000, 2: 500, 3: 99999},  # proces 3 dopiero się pojawił
            {1: 100, 3: 100000},          # PID 1 to już inny proces
        ])
        meter = autostop.RateMeter(lambda: next(samples))
        self.assertIsNone(meter.rate(0.0))
        self.assertEqual(meter.rate(10.0), 200.0)
        self.assertEqual(meter.rate(20.0), 0.1)


class DeciderTest(unittest.TestCase):
    def make(self):
        return autostop.Decider(idle=300, stall=1800, min_download=60)

    def test_waits_until_sustained_download(self):
        decider = self.make()
        self.assertEqual(decider.update(0, True, False).state, "waiting")
        self.assertEqual(decider.update(30, True, False).state, "waiting")
        self.assertEqual(decider.update(40, False, False).state, "waiting")  # tylko skok
        self.assertEqual(decider.update(50, True, False).state, "waiting")
        self.assertEqual(decider.update(110, True, False).state, "downloading")

    def test_finishes_after_idle_time(self):
        decider = self.make()
        decider.update(0, True, False)
        decider.update(60, True, False)
        verdict = decider.update(200, False, False)
        self.assertEqual((verdict.state, verdict.quiet_for, verdict.quiet_limit),
                         ("quiet", 140, 300))
        self.assertEqual(decider.update(359, False, False).state, "quiet")
        self.assertEqual(decider.update(360, False, False).state, "done")

    def test_activity_resets_quiet_time(self):
        decider = self.make()
        decider.update(0, False, True)
        decider.update(100, True, True)
        self.assertEqual(decider.update(399, False, False).state, "quiet")
        self.assertEqual(decider.update(400, False, False).state, "done")

    def test_queue_starts_watching_and_extends_patience(self):
        decider = self.make()
        self.assertEqual(decider.update(1000, False, True).state, "quiet")
        self.assertEqual(decider.update(1000 + 1799, False, True).state, "quiet")
        self.assertEqual(decider.update(1000 + 1800, False, True).state, "done")


class ShutdownCommandTest(unittest.TestCase):
    def test_windows(self):
        self.assertEqual(autostop.shutdown_command(False, "win32"), ["shutdown", "/s", "/t", "0"])
        self.assertEqual(autostop.shutdown_command(True, "win32")[-1], "/f")

    def test_macos(self):
        self.assertEqual(autostop.shutdown_command(False, "darwin")[0], "osascript")


class ArgsTest(unittest.TestCase):
    def test_defaults(self):
        args = autostop.parse_args([])
        self.assertEqual((args.idle, args.stall, args.threshold, args.dry_run),
                         (5, 30, 250, False))

    def test_test_alias_and_extra_processes(self):
        args = autostop.parse_args(["--test", "--process", "XboxPcApp.exe", "--process", "Foo"])
        self.assertTrue(args.dry_run)
        self.assertEqual(autostop.extra_process_names(args), {"xboxpcapp", "foo"})


if __name__ == "__main__":
    unittest.main()
