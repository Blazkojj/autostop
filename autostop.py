#!/usr/bin/env python3
"""AutoStop - wyłącza komputer, gdy gry skończą się pobierać.

Program obserwuje launchery gier (Steam, Epic Games, GOG Galaxy, Battle.net,
EA app, Ubisoft Connect, ...). Gdy przez kilka minut nic się już nie pobiera,
odlicza minutę i wyłącza komputer.

Skąd wie, że coś się pobiera?
  1. Mierzy, ile danych na sekundę zapisują/odczytują procesy launcherów
     (pobierana gra musi trafić na dysk, więc to działa dla każdego launchera).
  2. Czyta pliki Steam (appmanifest_*.acf) i Epic (*.item), żeby wiedzieć,
     które gry są jeszcze w kolejce i ile procent już pobrano.

Użycie:
  python autostop.py            uruchom z domyślnymi ustawieniami
  python autostop.py --test     tryb testowy - wszystko działa, ale bez wyłączania
  python autostop.py --status   pokaż, co program widzi, i zakończ
  python autostop.py --help     wszystkie opcje
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

try:
    import psutil
except ImportError:  # komunikat dla użytkownika jest w main()
    psutil = None


# Nazwy procesów launcherów (małe litery, bez ".exe"). Ich ruch na dysku
# oznacza, że coś się pobiera lub instaluje.
LAUNCHER_PROCESSES = {
    "steam", "steamservice",                    # Steam
    "epicgameslauncher",                        # Epic Games
    "galaxyclient", "galaxyclientservice",      # GOG Galaxy
    "battle.net",                               # Battle.net
    "eadesktop", "eabackgroundservice",         # EA app
    "upc", "ubisoftconnect",                    # Ubisoft Connect
    "rockstarservice",                          # Rockstar Games Launcher
    "heroic", "legendary", "gogdl",             # Heroic (Linux)
}

# Procesy o zbyt ogólnych nazwach - liczą się tylko, gdy ścieżka do pliku .exe
# zawiera podany fragment (np. "Agent.exe" to pobieracz Battle.net).
LAUNCHER_PROCESSES_BY_PATH = {
    "agent": "battle.net",
}

# Flagi stanu gry w plikach Steam appmanifest_*.acf (pole "StateFlags").
STEAM_UPDATE_RUNNING = 0x100
STEAM_UPDATE_PAUSED = 0x200
STEAM_UPDATE_STARTED = 0x400
STEAM_RECONFIGURING = 0x10000
STEAM_VALIDATING = 0x20000
STEAM_ADDING_FILES = 0x40000
STEAM_PREALLOCATING = 0x80000
STEAM_DOWNLOADING = 0x100000
STEAM_STAGING = 0x200000
STEAM_COMMITTING = 0x400000
STEAM_IN_PROGRESS = (
    STEAM_UPDATE_RUNNING | STEAM_UPDATE_STARTED | STEAM_RECONFIGURING
    | STEAM_VALIDATING | STEAM_ADDING_FILES | STEAM_PREALLOCATING
    | STEAM_DOWNLOADING | STEAM_STAGING | STEAM_COMMITTING
)


# --------------------------------------------------------------------------
# Steam
# --------------------------------------------------------------------------

def parse_vdf(text: str) -> dict:
    """Minimalny parser formatu KeyValues (VDF/ACF), w którym Steam zapisuje
    swoje pliki. Klucze są zamieniane na małe litery."""
    tokens = _tokenize_vdf(text)
    result, _ = _parse_vdf_block(tokens, 0)
    return result


def _tokenize_vdf(text: str) -> list:
    # Nawiasy klamrowe to "{" i "}", napisy to krotki ("s", wartość).
    tokens = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end == -1 else end + 1
        elif c in "{}":
            tokens.append(c)
            i += 1
        elif c == '"':
            i += 1
            buf = []
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    i += 1
                    buf.append({"n": "\n", "t": "\t"}.get(text[i], text[i]))
                else:
                    buf.append(text[i])
                i += 1
            tokens.append(("s", "".join(buf)))
            i += 1
        else:
            start = i
            while i < n and not text[i].isspace() and text[i] not in '{}"':
                i += 1
            tokens.append(("s", text[start:i]))
    return tokens


def _parse_vdf_block(tokens: list, pos: int) -> tuple[dict, int]:
    result = {}
    while pos < len(tokens):
        token = tokens[pos]
        if token == "}":
            return result, pos + 1
        if token == "{":  # uszkodzony plik - pomijamy
            pos += 1
            continue
        key = token[1].lower()
        pos += 1
        if pos >= len(tokens) or tokens[pos] == "}":
            result[key] = ""
            continue
        if tokens[pos] == "{":
            result[key], pos = _parse_vdf_block(tokens, pos + 1)
        else:
            result[key] = tokens[pos][1]
            pos += 1
    return result, pos


def find_steam_root(override: str | None = None) -> Path | None:
    """Zwraca folder instalacji Steam albo None, jeśli go nie ma."""
    candidates = []
    if override:
        candidates.append(Path(override))
    if sys.platform == "win32":
        candidates += _steam_paths_from_registry()
        for env in ("ProgramFiles(x86)", "ProgramFiles"):
            if os.environ.get(env):
                candidates.append(Path(os.environ[env]) / "Steam")
    elif sys.platform == "darwin":
        candidates.append(Path.home() / "Library/Application Support/Steam")
    else:
        home = Path.home()
        candidates += [
            home / ".steam/steam",
            home / ".local/share/Steam",
            home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
            home / "snap/steam/common/.local/share/Steam",
        ]
    for path in candidates:
        if (path / "steamapps").is_dir():
            return path
    return None


def _steam_paths_from_registry() -> list[Path]:
    import winreg

    keys = [
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    ]
    paths = []
    for hive, key, value in keys:
        try:
            with winreg.OpenKey(hive, key) as handle:
                paths.append(Path(winreg.QueryValueEx(handle, value)[0]))
        except OSError:
            pass
    return paths


def steam_library_dirs(steam_root: Path) -> list[Path]:
    """Zwraca foldery "steamapps" wszystkich bibliotek Steam (gry mogą leżeć
    na kilku dyskach)."""
    dirs = [steam_root / "steamapps"]
    try:
        text = (steam_root / "steamapps" / "libraryfolders.vdf").read_text(
            encoding="utf-8", errors="replace")
        folders = parse_vdf(text).get("libraryfolders", {})
    except OSError:
        folders = {}
    for key, entry in folders.items():
        if isinstance(entry, dict):
            path = entry.get("path")
        elif key.isdigit():  # stary format: "1" "D:\\SteamLibrary"
            path = entry
        else:
            path = None
        if path:
            dirs.append(Path(path) / "steamapps")

    unique, seen = [], set()
    for d in dirs:
        key = os.path.normcase(os.path.abspath(d))
        if key not in seen and d.is_dir():
            seen.add(key)
            unique.append(d)
    return unique


@dataclass
class PendingGame:
    launcher: str
    name: str
    progress: float | None = None  # 0.0 - 1.0, jeśli wiadomo

    def label(self) -> str:
        if self.progress is None:
            return f"{self.name} [{self.launcher}]"
        return f"{self.name} [{self.launcher} {self.progress:.0%}]"


def parse_steam_manifest(text: str) -> PendingGame | None:
    """Zwraca grę, jeśli jest w trakcie pobierania/instalacji (i nie jest
    wstrzymana), w przeciwnym razie None."""
    state = parse_vdf(text).get("appstate")
    if not isinstance(state, dict):
        return None
    flags = _to_int(state.get("stateflags"))
    if not flags & STEAM_IN_PROGRESS or flags & STEAM_UPDATE_PAUSED:
        return None
    name = state.get("name") or f"App {state.get('appid', '?')}"
    total = _to_int(state.get("bytestodownload"))
    done = _to_int(state.get("bytesdownloaded"))
    progress = min(done / total, 1.0) if total > 0 else None
    return PendingGame("Steam", name, progress)


def scan_steam(library_dirs: list[Path]) -> list[PendingGame]:
    games = []
    for library in library_dirs:
        for manifest in sorted(library.glob("appmanifest_*.acf")):
            try:
                text = manifest.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            game = parse_steam_manifest(text)
            if game:
                games.append(game)
    return games


def _to_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------------------
# Epic Games
# --------------------------------------------------------------------------

def find_epic_manifest_dir() -> Path | None:
    if sys.platform == "win32":
        base = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Epic"
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support/Epic"
    else:
        return None
    path = base / "EpicGamesLauncher" / "Data" / "Manifests"
    return path if path.is_dir() else None


def parse_epic_item(text: str) -> PendingGame | None:
    """Zwraca grę, jeśli Epic oznaczył jej instalację jako niedokończoną."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or not data.get("bIsIncompleteInstall"):
        return None
    name = data.get("DisplayName") or data.get("AppName") or "?"
    return PendingGame("Epic", name)


def scan_epic(manifest_dir: Path) -> list[PendingGame]:
    games = []
    for item in sorted(manifest_dir.glob("*.item")):
        try:
            text = item.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        game = parse_epic_item(text)
        if game:
            games.append(game)
    return games


class QueueWatcher:
    """Sprawdza kolejki pobierania Steam i Epic."""

    def __init__(self, steam_path: str | None = None):
        self.steam_root = find_steam_root(steam_path)
        self.steam_libraries = steam_library_dirs(self.steam_root) if self.steam_root else []
        self.epic_dir = find_epic_manifest_dir()

    def pending(self) -> list[PendingGame]:
        games = scan_steam(self.steam_libraries)
        if self.epic_dir:
            games += scan_epic(self.epic_dir)
        return games


# --------------------------------------------------------------------------
# Ruch na dysku launcherów
# --------------------------------------------------------------------------

def is_launcher_process(name: str | None, exe_getter, extra: set[str]) -> bool:
    """Czy proces o tej nazwie to launcher gier? exe_getter() zwraca ścieżkę
    do pliku .exe (wywoływany tylko, gdy jest potrzebna)."""
    if not name:
        return False
    name = name.lower()
    if name.endswith(".exe"):
        name = name[:-4]
    if name in LAUNCHER_PROCESSES or name in extra:
        return True
    fragment = LAUNCHER_PROCESSES_BY_PATH.get(name)
    if fragment:
        exe = exe_getter() or ""
        return fragment in exe.lower()
    return False


def launcher_processes(extra: set[str]):
    """Zwraca pary (proces, nazwa) dla uruchomionych launcherów."""
    # Bez process_iter(["name"]): wtedy błąd odczytu jednego procesu
    # przerwałby całe wyliczanie zamiast pominąć tylko ten proces.
    for proc in psutil.process_iter():
        try:
            name = proc.name()
            if is_launcher_process(name, proc.exe, extra):
                yield proc, name
        except (psutil.Error, OSError):
            continue


def launcher_io_sampler(extra: set[str]):
    """Zwraca funkcję, która podaje {pid: bajty odczytane+zapisane} dla
    wszystkich uruchomionych launcherów."""

    def sample() -> dict[int, int]:
        totals = {}
        for proc, _ in launcher_processes(extra):
            try:
                io = proc.io_counters()
            except (psutil.Error, OSError):
                continue
            totals[proc.pid] = io.read_bytes + io.write_bytes
        return totals

    return sample


def network_sampler() -> dict[int, int]:
    """Zapas dla systemów bez licznika I/O procesów (macOS): cały ruch
    przychodzący z sieci."""
    return {0: psutil.net_io_counters().bytes_recv}


def running_launchers(extra: set[str]) -> list[str]:
    names = {name for _, name in launcher_processes(extra)}
    return sorted(names, key=str.lower)


class RateMeter:
    """Liczy prędkość (bajty/s) na podstawie kolejnych odczytów liczników."""

    def __init__(self, sampler):
        self._sampler = sampler
        self._previous: tuple[float, dict[int, int]] | None = None

    def rate(self, now: float) -> float | None:
        current = self._sampler()
        previous, self._previous = self._previous, (now, current)
        if previous is None:
            return None
        then, before = previous
        # Liczymy tylko procesy widziane w obu odczytach; licznik mniejszy niż
        # poprzednio oznacza nowy proces o tym samym PID.
        delta = sum(max(0, value - before[pid])
                    for pid, value in current.items() if pid in before)
        elapsed = now - then
        return delta / elapsed if elapsed > 0 else 0.0


# --------------------------------------------------------------------------
# Decyzja: kiedy wyłączyć
# --------------------------------------------------------------------------

@dataclass
class Verdict:
    state: str              # "waiting" | "downloading" | "quiet" | "done"
    quiet_for: float = 0.0  # ile sekund nic się nie pobiera
    quiet_limit: float = 0.0


class Decider:
    """Postanawia, czy pobieranie się skończyło.

    - Najpierw czeka, aż pobieranie się zacznie: coś jest w kolejce Steam/Epic
      albo launchery mielą dysk nieprzerwanie przez min_download sekund
      (żeby samo uruchomienie Steama nie liczyło się jako pobieranie).
    - Potem "koniec" to brak ruchu przez idle sekund. Jeśli Steam/Epic wciąż
      mają coś w kolejce, czekamy dłużej (stall sekund) - to może być chwilowa
      przerwa, np. zerwany internet.
    """

    def __init__(self, idle: float, stall: float, min_download: float):
        self.idle = idle
        self.stall = max(stall, idle)
        self.min_download = min_download
        self.started = False
        self._active_since: float | None = None
        self._last_active = 0.0

    def update(self, now: float, active: bool, pending: bool) -> Verdict:
        if active:
            if self._active_since is None:
                self._active_since = now
            self._last_active = now
        else:
            self._active_since = None

        if not self.started:
            sustained = active and now - self._active_since >= self.min_download
            if not (pending or sustained):
                return Verdict("waiting")
            self.started = True
            self._last_active = now

        if active:
            return Verdict("downloading")
        limit = self.stall if pending else self.idle
        quiet = now - self._last_active
        return Verdict("done" if quiet >= limit else "quiet", quiet, limit)


# --------------------------------------------------------------------------
# Wyłączanie
# --------------------------------------------------------------------------

def shutdown_command(force: bool, platform: str = sys.platform) -> list[str]:
    if platform == "win32":
        command = ["shutdown", "/s", "/t", "0"]
        if force:
            command.append("/f")
        return command
    if platform == "darwin":
        return ["osascript", "-e", 'tell application "System Events" to shut down']
    if shutil.which("systemctl"):
        return ["systemctl", "poweroff"]
    return ["shutdown", "-h", "now"]


# --------------------------------------------------------------------------
# Wyświetlanie
# --------------------------------------------------------------------------

class Console:
    """Jedna linia statusu nadpisywana w miejscu + zwykłe komunikaty."""

    def __init__(self):
        self.tty = sys.stdout.isatty()
        self._status_shown = False
        self._last_plain_status = 0.0

    def message(self, text: str) -> None:
        self._clear_status()
        print(f"[{datetime.now():%H:%M:%S}] {text}", flush=True)

    def status(self, text: str) -> None:
        if self.tty:
            width = shutil.get_terminal_size().columns - 1
            sys.stdout.write("\r" + text[:width].ljust(width))
            sys.stdout.flush()
            self._status_shown = True
        elif time.monotonic() - self._last_plain_status >= 60:
            # Gdy wyjście idzie do pliku, nie zapychamy go co 10 sekund.
            self._last_plain_status = time.monotonic()
            print(f"[{datetime.now():%H:%M:%S}] {text}", flush=True)

    def _clear_status(self) -> None:
        if self._status_shown:
            width = shutil.get_terminal_size().columns - 1
            sys.stdout.write("\r" + " " * width + "\r")
            self._status_shown = False


def format_rate(bytes_per_second: float | None) -> str:
    if bytes_per_second is None:
        return "..."
    if bytes_per_second >= 1024 * 1024:
        return f"{bytes_per_second / 1024 / 1024:.1f} MB/s"
    return f"{bytes_per_second / 1024:.0f} KB/s"


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def describe_queue(games: list[PendingGame], limit: int = 3) -> str:
    if not games:
        return ""
    labels = [g.label() for g in games[:limit]]
    if len(games) > limit:
        labels.append(f"+{len(games) - limit} więcej")
    return " | W kolejce: " + ", ".join(labels)


STATE_MESSAGES = {
    "waiting": "Czekam, aż zacznie się pobieranie gier...",
    "downloading": "Gry się pobierają.",
    "quiet": "Pobieranie ustało. Jeśli nic nie ruszy, wyłączę komputer.",
    "done": "Pobieranie zakończone!",
}


# --------------------------------------------------------------------------
# Program
# --------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="autostop",
        description="Wyłącza komputer, gdy gry skończą się pobierać "
                    "(Steam, Epic, GOG, Battle.net, EA, Ubisoft...).",
    )
    parser.add_argument("--test", "--dry-run", dest="dry_run", action="store_true",
                        help="tryb testowy: wszystko działa, ale komputer NIE zostanie wyłączony")
    parser.add_argument("--status", action="store_true",
                        help="pokaż, co program widzi (launchery, kolejki), i zakończ")
    parser.add_argument("--idle", type=float, default=5, metavar="MIN",
                        help="po ilu minutach bez pobierania wyłączyć komputer (domyślnie 5)")
    parser.add_argument("--stall", type=float, default=30, metavar="MIN",
                        help="ile minut czekać, gdy Steam/Epic mają coś w kolejce, "
                             "ale nic się nie pobiera (domyślnie 30)")
    parser.add_argument("--threshold", type=float, default=250, metavar="KB/s",
                        help="poniżej tej prędkości uznajemy, że nic się nie pobiera "
                             "(domyślnie 250 KB/s)")
    parser.add_argument("--countdown", type=int, default=60, metavar="SEK",
                        help="odliczanie przed wyłączeniem, można je przerwać Ctrl+C "
                             "(domyślnie 60 s)")
    parser.add_argument("--interval", type=float, default=10, metavar="SEK",
                        help="co ile sekund sprawdzać (domyślnie 10)")
    parser.add_argument("--min-download", type=float, default=60, metavar="SEK",
                        help="ile sekund ciągłego ruchu uznać za start pobierania, gdy "
                             "nic nie ma w kolejce Steam/Epic (domyślnie 60)")
    parser.add_argument("--process", action="append", default=[], metavar="NAZWA",
                        help="dodatkowy proces launchera do obserwowania, np. "
                             "--process MojLauncher (można podać kilka razy)")
    parser.add_argument("--steam-path", metavar="FOLDER",
                        help="folder instalacji Steam, jeśli nie zostanie znaleziony sam")
    parser.add_argument("--force", action="store_true",
                        help="wymuś zamknięcie programów przy wyłączaniu (niezapisane "
                             "dane w innych programach przepadną)")
    args = parser.parse_args(argv)
    for name in ("idle", "stall", "threshold", "interval"):
        if getattr(args, name) <= 0:
            parser.error(f"--{name} musi być większe od zera")
    if args.countdown < 0 or args.min_download < 0:
        parser.error("--countdown i --min-download nie mogą być ujemne")
    return args


def extra_process_names(args: argparse.Namespace) -> set[str]:
    names = set()
    for name in args.process:
        name = name.lower()
        names.add(name[:-4] if name.endswith(".exe") else name)
    return names


def make_meter(extra: set[str]) -> tuple[RateMeter, str]:
    if hasattr(psutil.Process, "io_counters"):
        return RateMeter(launcher_io_sampler(extra)), "ruch na dysku launcherów"
    return RateMeter(network_sampler), "cały ruch sieciowy (ten system nie podaje I/O procesów)"


def print_setup(watcher: QueueWatcher, source: str, args: argparse.Namespace) -> None:
    print("AutoStop - wyłączę komputer, gdy gry się pobiorą.")
    if watcher.steam_root:
        print(f"  Steam:   {watcher.steam_root} (bibliotek: {len(watcher.steam_libraries)})")
    else:
        print("  Steam:   nie znaleziono (użyj --steam-path, jeśli jest zainstalowany)")
    print(f"  Epic:    {watcher.epic_dir or 'nie znaleziono'}")
    print(f"  Mierzę:  {source}")
    print(f"  Koniec:  {args.idle:g} min bez pobierania "
          f"({args.stall:g} min, jeśli coś czeka w kolejce Steam/Epic)")
    if args.dry_run:
        print("  TRYB TESTOWY: komputer NIE zostanie wyłączony.")
    print("Ctrl+C - przerwij w dowolnym momencie.\n")


def show_status(args: argparse.Namespace) -> int:
    extra = extra_process_names(args)
    watcher = QueueWatcher(args.steam_path)
    meter, source = make_meter(extra)
    print_setup(watcher, source, args)
    for library in watcher.steam_libraries:
        print(f"  Biblioteka Steam: {library}")
    print(f"  Uruchomione launchery: {', '.join(running_launchers(extra)) or 'brak'}")
    games = watcher.pending()
    print(f"  W kolejce: {', '.join(g.label() for g in games) or 'nic'}")
    print("  Mierzę prędkość przez 5 sekund...")
    meter.rate(time.monotonic())
    time.sleep(5)
    print(f"  Prędkość: {format_rate(meter.rate(time.monotonic()))}")
    return 0


def watch(args: argparse.Namespace, console: Console) -> bool:
    """Czeka, aż pobieranie się skończy. Zwraca True, gdy trzeba wyłączyć."""
    extra = extra_process_names(args)
    watcher = QueueWatcher(args.steam_path)
    meter, source = make_meter(extra)
    decider = Decider(idle=args.idle * 60, stall=args.stall * 60,
                      min_download=args.min_download)
    threshold = args.threshold * 1024
    print_setup(watcher, source, args)

    last_state = None
    while True:
        now = time.monotonic()
        speed = meter.rate(now)
        games = watcher.pending()
        active = speed is not None and speed >= threshold
        verdict = decider.update(now, active, bool(games))

        if verdict.state != last_state:
            if verdict.state == "quiet" and last_state in (None, "waiting"):
                console.message("Gry czekają w kolejce - obserwuję pobieranie.")
            else:
                console.message(STATE_MESSAGES[verdict.state])
            last_state = verdict.state
        if verdict.state == "done":
            return True

        queue = describe_queue(games)
        if verdict.state == "waiting":
            console.status(f"Czekam na pobieranie (teraz {format_rate(speed)}){queue}")
        elif verdict.state == "downloading":
            console.status(f"Pobieranie: {format_rate(speed)}{queue}")
        else:
            console.status(f"Cisza od {format_duration(verdict.quiet_for)}, wyłączę po "
                           f"{format_duration(verdict.quiet_limit)}{queue}")
        time.sleep(args.interval)


def countdown(seconds: int, console: Console) -> None:
    for remaining in range(seconds, 0, -1):
        console.status(f"Wyłączam komputer za {remaining} s - naciśnij Ctrl+C, aby anulować.")
        time.sleep(1)


def main(argv: list[str] | None = None) -> int:
    # Nazwy gier mogą mieć znaki, których nie ma w kodowaniu konsoli.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    args = parse_args(argv)
    if psutil is None:
        print("Brakuje biblioteki psutil. Zainstaluj ją poleceniem:\n"
              "  py -m pip install psutil      (Windows)\n"
              "  python3 -m pip install psutil (Linux/macOS)")
        return 1

    console = Console()
    try:
        if args.status:
            return show_status(args)
        watch(args, console)
        countdown(args.countdown, console)
    except KeyboardInterrupt:
        console.message("Anulowano - komputer nie zostanie wyłączony.")
        return 0

    command = shutdown_command(args.force)
    if args.dry_run:
        console.message("TRYB TESTOWY: tutaj komputer zostałby wyłączony "
                        f"({' '.join(command)}).")
        return 0
    console.message("Wyłączam komputer...")
    try:
        subprocess.run(command, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        console.message(f"Nie udało się wyłączyć komputera: {error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
