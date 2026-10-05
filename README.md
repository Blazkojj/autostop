# AutoStop

Program, który **sam wyłącza komputer, gdy gry skończą się pobierać**.
Włączasz pobieranie w Steamie (albo w Epic Games, GOG Galaxy, Battle.net,
EA app, Ubisoft Connect), uruchamiasz AutoStop i idziesz spać. Po pobraniu komputer się wyłączy.

## Jak to działa

Co 10 sekund program sprawdza dwie rzeczy:

1. **Ruch na dysku launcherów.** Pobierana gra musi zostać zapisana na dysk,
   więc AutoStop mierzy, ile danych na sekundę zapisują procesy launcherów
   (Steam, Epic Games, GOG Galaxy, Battle.net, EA app, Ubisoft Connect,
   Rockstar). To działa dla każdego z nich.
2. **Kolejkę pobierania Steam i Epic.** Czyta pliki tych launcherów i wie,
   które gry są jeszcze w kolejce i ile procent już pobrano.

Gdy przez **5 minut** nic się nie pobiera, program odlicza **60 sekund**
i wyłącza komputer. Odliczanie można przerwać klawiszami `Ctrl+C`.

Jeśli Steam albo Epic mają jeszcze coś w kolejce, ale nic się nie pobiera
(np. padł internet), program czeka dłużej, bo **30 minut**. Gdy pobieranie wróci,
odliczanie zaczyna się od nowa.

Gry **wstrzymane** w Steamie nie blokują wyłączenia.

## Instalacja (Windows)

1. Zainstaluj Pythona: <https://www.python.org/downloads/>.
   Przy instalacji zaznacz **„Add python.exe to PATH”**.
2. Pobierz ten projekt (zielony przycisk **Code → Download ZIP**) i rozpakuj.
3. Kliknij dwa razy **`start.bat`**. Za pierwszym razem program sam doinstaluje
   bibliotekę `psutil`.

## Użycie

Najpierw włącz pobieranie gier, potem uruchom `start.bat`. Kolejność nie jest
ważna: jeśli uruchomisz AutoStop wcześniej, poczeka, aż pobieranie się zacznie.

Za pierwszym razem sprawdź, czy wszystko działa, **w trybie testowym**. Robi wszystko
tak samo, tylko na końcu nie wyłącza komputera:

```
start.bat --test
```

Aby zobaczyć, co program widzi (znalezione biblioteki Steam, gry w kolejce,
uruchomione launchery, aktualna prędkość):

```
start.bat --status
```

Z wiersza poleceń można też uruchomić program bezpośrednio: `py autostop.py`.

### Opcje

| Opcja | Domyślnie | Co robi |
|---|---|---|
| `--test` | | tryb testowy, bez wyłączania komputera |
| `--status` | | pokazuje, co program widzi, i kończy działanie |
| `--idle MIN` | 5 | po ilu minutach bez pobierania wyłączyć komputer |
| `--stall MIN` | 30 | ile minut czekać, gdy Steam/Epic mają coś w kolejce, ale nic się nie pobiera |
| `--countdown SEK` | 60 | odliczanie przed wyłączeniem |
| `--threshold KB/s` | 250 | poniżej tej prędkości uznajemy, że nic się nie pobiera |
| `--interval SEK` | 10 | co ile sekund sprawdzać |
| `--min-download SEK` | 60 | ile sekund ciągłego ruchu oznacza start pobierania, gdy nic nie ma w kolejce Steam/Epic |
| `--process NAZWA` | | dodatkowy proces launchera do obserwowania (można podać kilka razy) |
| `--steam-path FOLDER` | | folder Steama, jeśli program nie znajdzie go sam |
| `--force` | | wymusza zamknięcie programów przy wyłączaniu (**niezapisane dane w innych programach przepadną**) |

Przykład: wyłącz po 10 minutach ciszy i odliczaj 2 minuty:

```
start.bat --idle 10 --countdown 120
```

## Pytania

**Komputer się nie wyłączył, bo jakiś program „blokuje zamykanie”.**
Windows czeka, aż otwarte programy zapiszą dane. Użyj opcji `--force`, jeśli
na pewno nie masz niczego niezapisanego.

**Mój launcher nie jest na liście.**
Sprawdź w Menedżerze zadań nazwę jego procesu i dodaj ją, np.
`start.bat --process NazwaProcesu`. Aplikacja Xbox i Microsoft Store pobierają
gry przez usługi systemowe Windows, więc AutoStop ich nie obsługuje.

**Program uznał, że pobieranie się skończyło, a ono jeszcze trwało.**
Przy bardzo wolnym internecie pobieranie może schodzić poniżej 250 KB/s.
Zmniejsz próg, np. `--threshold 50`, albo wydłuż czas: `--idle 15`.

**Działa na Linuksie?**
Tak: `python3 -m pip install psutil`, potem `python3 autostop.py`.
Na macOS program zamiast ruchu na dysku launcherów mierzy cały ruch sieciowy.

## Własny plik .exe

Jeśli chcesz mieć jeden plik `.exe`, który działa bez Pythona:

```
py -m pip install pyinstaller psutil
py -m PyInstaller --onefile autostop.py
```

Gotowy plik pojawi się w folderze `dist`.

## Testy

```
python -m unittest discover -s tests
```
