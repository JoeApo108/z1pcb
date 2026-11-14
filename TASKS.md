# z1pcb — PCB CAM pre Makera Z1 (Windows 11, osobné použitie: Josef + známy)

## Infra
- Windows build: GitHub Actions (`.github/workflows/windows.yml`), spúšťa `tools/release.sh`. Lab VM 7112 zmazaná 2026-11-14.

## Fáza 1 — jadro (hotové)
- [x] Gerber (gerbonara 1.6.3) + Excellon → shapely; polarita LPD/LPC, oblúky, výrezy obrysu, drážky G85
- [x] Dráhy: Flat 2 mm vyčistí všetku voľnú meď, rytec izoluje každý obrys medi + dočistí zvyšky, Corn 0,8 otvory/drážky/výrezy/obrys s 4 mostíkmi
- [x] G-kód Z1 (Smoothieware): `T# M6` meria nástroj sám, koniec `M05 G28 M02`, hlavička `;@MKR`, `;Z1PCB|TRANSFORM`
- [x] Kontroly z G-kódu (aj Studio): zásah do medi, voľná meď, skraty, otvory, výrezy, obrys; registrácia podľa hlavičky alebo otvorov
- [x] Testy: IR doska OK (meď 0, skraty 0, otvory 150/150, výrezy 100 %, obrys 97 %); Studio 0.1.3.0 export: 1 skrat (GND + U1 21–30), výrezy 0 %
- [x] Windows 11: testy 3/3; generovanie 48 s na Xeon Gold 6138 (Mac 7 s)

## Fáza 2 — rozhranie
- [x] PySide6 6.12 (Essentials): priečinok/drag&drop s rozpoznaním vrstiev (X2 FileFunction, názvy KiCad/EasyEDA/Altium/Eagle), strana, polotovar, nástroje a parametre, výpočet na pozadí s priebehom
- [x] Náhľad: doska, meď, dráhy T4/T2/T3, problémy z kontroly červeno; zoom kolieskom, posun ťahaním
- [x] Export `.cnc`, súhrn kontroly (OK / problémy), dĺžka rezu a odhad času; kontrola cudzieho G-kódu (Studio)
- [x] Samotest `python -m z1pcb.gui --selftest <priečinok> --screenshot x.png`: Mac OK, Windows 11 OK (65 s na Xeon Gold 6138; offscreen na Windows potrebuje `QT_QPA_FONTDIR=C:\Windows\Fonts`)
- [ ] Vyskúšať naživo (používateľ cez konzolu Proxmoxu / RDP)

## Fáza 3 — distribúcia
- [x] PyInstaller 6.22.3 (wheely cez safe-chain, SHA-256 overené vo VM) + Inno Setup 6.7.4 (digest z GitHubu + podpis Pyrsys B.V. overené); `tools/build_windows.ps1`
- [x] Inštalácia bez admin práv do `%LOCALAPPDATA%\Programs\Z1 PCB`, ponuka Štart, odinštalátor, SK/CZ/EN; 35 MB inštalačka, 127 MB nainštalované
- [x] Nainštalovaná aplikácia: samotest OK (64 s)
- [x] Aktualizácie cez GitHub Releases (`/releases/latest`, asset `*-setup.exe`, overenie `digest` sha256), tichá inštalácia a reštart; menu Pomoc → Skontrolovať aktualizácie
- [x] Test aktualizácie (mock server s formátom GitHub API): 0.1.0 → 0.1.1 OK + reštart; zlý digest odmietnutý; bez novšej verzie nič
- [ ] GitHub repozitár na vydania (verejný, len inštalačky) + vydanie v0.1.1 — čaká na súhlas
- [ ] Bez podpisu kódu: SmartScreen „Ďalšie informácie → Spustiť aj tak“
- [ ] Návod pre známeho (krátky)

## Fáza 4 — simulácia (0.2.0)
- [x] `z1pcb/sim.py`: časovanie podľa limitov Z1 (X/Y 1200, Z 600 mm/min, výmena nástroja ≈ 60 s), výšková mapa 0,05 mm, tvar nástroja (valec / kužeľ rytca), zmenené oblasti, snímky na posun späť
- [x] Záložka Simulácia: prehrať/pozastaviť (medzerník), rýchlosť 1×–1000×/Max, časová os, skok na výmenu nástroja, zastavenie pri výmene, nástroj, Z, posuv, riadok
- [x] Test: simulácia zodpovedá návrhu (otvory prerezané, meď na plôškach ostane, voľná meď preč)
- [x] Opravy nájdené simuláciou: dvojité `T3 M6` (zbytočné zastavenie); koliesko myši menilo hodnoty polí pri posúvaní panela
- [x] Windows 0.2.0: inštalácia + samotest so simuláciou OK

## Otvorené (na stroji)
- [ ] Autoleveling pri vlastnom G-kóde (Studio pri spustení, alebo `M495` v súbore)
- [ ] Prvá skúšobná doska; prezvoniť skraty multimetrom

## Fáza 5 — opravy z revízie 2026-11-13 (0.3.0)
Stav: 60 testov na Macu aj na Windows 11 (VM), build 0.3.0 z wheelov zamknutých odtlačkami, podpísaný. Nič nie je commitnuté.
### Logika
- [x] L1 Neaktuálny G-kód: každá zmena nastavenia alebo súborov ruší výsledok (`Main.invalidate`, epocha); výsledok starého jobu sa zahodí; G-kód sa drží v pamäti, nie v spoločnom dočasnom súbore; uloženie po neúspešnej kontrole pýta potvrdenie
- [x] L2 Pády: naraz beží jeden job (`Main.start`), okno pred zavretím počká na vlákna (`stop_threads`); `tests/scenarios.py`
- [x] L3 Obrys: konce do 0,02 mm sa spoja, neuzavretý obrys je chyba s miestom voľného konca, meď a otvory musia ležať v doske (`gerberio.validate`)
- [x] L4 Režim bez čistenia: voľné pásiky užšie než 0,30 mm sa odfrézujú (IR doska: 70 mm² → 0), kontrola hlási pásiky užšie než 0,25 mm; čas 21,6 → 26,2 min
- [x] L5 Prah obrysu z rozloženia mostíkov (`outline_min_pct`); mostíky najviac polovica obvodu
- [x] L6 Jeden čítač G-kódu `gparse` (modálny zápis, G2/G3, G20, T a M6 v ľubovoľnom tvare); kontrola odmietne súbor bez rozmerov nástrojov; zarovnanie podľa otvorov overuje zhodu
- [x] L7 Otvory menšie než fréza: riadok „pozor“ s počtom a priemermi; výrez užší než fréza je chyba
- [x] L8 Dosky bez vŕtania; v rozhraní poznámka, že sa frézuje jedna strana
- [x] L9 Výška priblíženia 0,5 mm (prejazdy ostávajú na 2 mm): 54,3 → 46,9 min; jeden odhad času (z `gparse`)
### Security
- [x] S1 Podpis vydania ed25519: kľúč `z1pcb-release-signing-key` v csec (guarded), verejná časť v `updates.py`; aplikácia overí podpis mena, verzie, veľkosti a odtlačku; `tools/sign_release.py`. Test vo VM: 0.2.99 → podpísaná 0.3.0 nainštalovaná a spustená; pozmenený podpis odmietnutý (exit 3); inštalačka dlhšia o bajt odmietnutá (exit 4)
- [x] S2 Testovací server len cez `--update-test http://127.0.0.1:…`; meno súboru podľa vzoru, sťahovanie len z repozitára vydaní, čerstvý dočasný priečinok
- [x] S3 HTTPS na GitHub zo zabalenej verzie: `Z1PCB.exe --update-check` vo VM → odpoveď HTTP 404 (repozitár ešte nie je), TLS funguje
- [x] S4 `requirements.txt` = 8 balíkov, inštalácia `--no-deps`; Windows build len z `wheels/` s `--require-hashes` (`tools/fetch_wheels.sh` cez safe-chain)
- [x] S5 Infra údaje a `vmexec.py` sú v `private/` (ignorované); `vmexec.py` overuje odtlačok certifikátu Proxmoxu. Verejný repozitár začína novou históriou; stará je len lokálne v `private/` (bundle)
### UX
- [x] U1 Desatinná bodka aj čiarka
- [x] U2 Náhľad: polotovar, nulový bod, mostíky; „Postup na stroji“ (F1); umiestnenie v strede alebo v rohu
- [x] U3 Nastavenia sa ukladajú (QSettings); polotovar štandardne najmenší možný podľa dosky
- [x] U4 Rezné podmienky: posuv, zanorenie, otáčky pre každý nástroj, hĺbka na prechod, výška priblíženia
- [x] U5 Simulácia: klik na časovú os, medzerník aj po úprave poľa, mapa len nad frézovanou plochou (veľkosť polotovaru už pamäť neovplyvňuje), snímky najviac 256 MB
- [x] U6 Chyby po slovensky (`UserError`), log v `%LOCALAPPDATA%\Z1 PCB\logs` (Pomoc → Otvoriť priečinok s logom), nezachytené chyby idú do logu a stavového riadku
- [x] U7 Rozpoznanie vrstiev číta len prvých 4000 bajtov
### DX
- [x] D1 Verejný repozitár `JoeApo108/z1pcb` (nová história), vydania v `JoeApo108/z1pcb-releases`
- [x] D2 Jeden čítač G-kódu, jedna trieda `Tool`, verdikt v `checks.verdict`, spoločný `job.make` / `job.check_file` pre okno aj CLI
- [x] D3 Testy: 60 (jadro, CLI, simulácia, okno bez obrazovky, aktualizácie). Chýbajú Gerbery z iných programov než KiCad
- [x] D4 `tools/release.sh`, `README.md`, `pyproject.toml`
- [x] D5 Hlavička G-kódu: verzia, parametre, rezné podmienky, odtlačky vstupov; kontrola hlási G-kód z iných súborov
- [x] D6 `ruff format` + `ruff check`
- [x] D7 Odstránené: `bench_out.cnc`, 17 pinov, nepoužité importy, nepoužitá premenná

### Otvorené po revízii
- [x] Zverejnená v0.3.0 v `JoeApo108/z1pcb-releases`
- [x] 0.3.1: licencie tretích strán v inštalačke (`tools/collect_licenses.py` → `licenses/`, `THIRD-PARTY.txt`; texty LGPL/GPL/Apache, ktoré wheely nemajú, sú v `licenses/`); Pomoc → Licencie
- [x] Licencia projektu: MIT
- [x] README a komentáre v kóde po anglicky
- [ ] Záloha podpisového kľúča mimo tohto Macu (bez neho sa nainštalované kópie samy neaktualizujú)
- [ ] Gerbery z EasyEDA/Altium/Eagle: skutočné exporty do testov

## Fáza 6 — čas frézovania a hrúbka medi (0.3.2)
- [x] Krátke presuny (do 5 mm) vo výške priblíženia 0,5 mm, dlhé na 2 mm
- [x] Pri čistení všetkej medi: susedné dráhy do 5 mm sa spoja rezom naprieč, ak fréza na spojnici nezasiahne meď (`cam.link`); IR doska 746 spojení
- [x] Krok plochej frézy 63 % (Makera); IR doska 46,9 → 37,7 min, bez čistenia 26,5 → 24,6 min
- [x] Hrúbka medi 18 / 35 / 70 µm: nastaví hĺbku (0,05 / 0,07 / 0,10), kontrola počíta rez cez meď až 0,01 mm pod fóliou a hlási príliš plytkú hĺbku
- [x] Hodnoty rezania overené proti knižnici Makera Studio (`makera_library.db`); pre pertinax Makera nemá hodnoty kukuričnej frézy, voľba podkladu preto nie je
- [x] Knižnica Makera Studio v aplikácii (`makera_lib`): výber nástroja pre T4/T2/T3 nastaví priemer, uhol, otáčky, posuv, zanorenie, hĺbku na prechod a krok; ručná zmena hodnoty prepne na „Vlastné hodnoty“; bez Studia ručné zadanie. Cesta na Windows neoverená (hľadá sa v %APPDATA% a %LOCALAPPDATA%)
- [x] Krok plochej frézy v Rezných podmienkach (%)
- [x] Tlačidlá dialógov po slovensky (Qt preklad + vlastné texty pre QPlatformTheme, ktoré slovenský preklad Qt nemá); samotest zlyhá, ak sa preklad Qt nenačíta
- [x] README: screenshoty (`docs/`), zoznam funkcií
- [ ] Čistenie len vo vybranej zóne (napr. pásmo 230 V) — čaká na súhlas
- [ ] `M495` (výšková mapa) v súbore, predvolene vypnuté — čaká na súhlas

## Fáza 7 — nepájivá maska (0.3.3)
- [x] Vrstva masky (`B.Mask`/`F.Mask`, X2 `Soldermask`) sa rozpozná a načíta; otvory mimo dosky sú chyba
- [x] Voľba „S nepájivou maskou (2 programy)“: 1. program meď (T4, T2), 2. program odkrytie plôšok nástrojom T5, potom otvory, výrezy, obrys; uloženie `…_1_med.cnc` a `…_2_maska_otvory_obrys.cnc`
- [x] Nástroj na masku T5 (Makera „Solder Mask Removal“: hrot 0,3 mm, 6 000 ot./min, posuv 400, zanorenie 200, hĺbka 0,2) aj z knižnice Studia
- [x] Kontrola: plôšky odkryté (IR doska 99,7 %), maska odstránená mimo otvorov (0), škrabák sa nepočíta ako rez do medi; Studio nástroj „… Solder Mask …“ sa rozpozná
- [x] Simulácia: škrabák nemení výšku, pri T5 pokyn na nanesenie masky; postup na stroji s krokom masky
- [x] Náhľad kreslí ako chyby len to, čo kontrola počíta ako chybu
- [ ] Na stroji: ako Z1 zmeria pružiaci nástroj T5 po `M6`
- [ ] IR projekt: pridať `B.Mask` do exportu v `make_pcb_cnc.py` (mimo z1pcb) — čaká na súhlas
- [x] Z firmvéru 1.1.3: žiadny riadok G-kódu nad 128 znakov (hlavička rozdelená, mená nástrojov skrátené; firmvér kopíruje riadok do 130-bajtového buffera bez kontroly)
- [x] Odhad času ako firmvér: zrýchlenie 150 mm/s² pri rýchloposuvoch a zvislých pohyboch, vreteno 8 s po M3 a 4 s po M5
- [x] Postup na stroji a README: výšková mapa musí pokryť celý polotovar (mimo nej firmvér nekoriguje), meranie sondou nezastavovať (issue #7)

## Fáza 8 — z testovania na Windows (0.3.4)
- [x] Tooltip: čierny text na tmavom pozadí → explicitný štýl QToolTip; overené vo Windows relácii (kontrast 190)
- [x] Lišta náhľadu pri užšom okne pretekala (− + Prispôsobiť neboli vidieť) → zalamuje sa do dvoch riadkov (FlowLayout); test pri 1000 a 1440 px
- [x] Štýl lišty a päty simulácie sa bez selektora prenášal na všetky prvky (čiary pod tlačidlami vrstiev) → štýl cez #PreviewHead / #SimFoot

## Fáza 9 — drobnosti z testovania (tiež 0.3.4)
- [x] Mostíky sa kreslia ako priečka cez drážku obrysu (nie krúžok ako otvor), s vysvetlením pri myši
- [x] „Otvoriť súbory návrhu…“: stačí vybrať jeden súbor, načíta sa celý priečinok (výber priečinka vo Windows súbory neukazuje); výber priečinka ostal v menu
- [x] Súbor → Naposledy otvorené (8 priečinkov)
- [x] Veľkosť okna a delenie panelov sa pamätajú (overené ručne v okne macOS; offscreen Qt veľkosť okna nedrží)
- [x] Predvolené meno G-kódu z názvu návrhu a strany (`ir_lista_cnc_spodna.cnc`)
- [x] Klik na chybný riadok kontroly priblíži náhľad na miesto chyby (skraty, zásah do medi, nevyfrézovaná meď, pásiky, maska)
- [x] `tools/release.sh`: build a testy z `git archive HEAD` (prvý build 0.3.4 zachytil rozpracované súbory z pracovného stromu)
- [x] Zatvorenie počas dlhého výpočtu: namiesto násilného ukončenia vlákna (pri záťaži zamrzlo) sa uložia nastavenia a proces skončí (kód 75); 8 súbežných pokusov bez zamrznutia
- [x] Testy paralelne (pytest-xdist): Mac 192 s → 53 s; build VM 7112 má 20 jadier a 16 GB (predtým 4 a 8 GB)
- [x] Klik na „Vrchná“ bez medi vrchnej strany nezahodí výsledok; zrušený výsledok prepne náhľad zo Simulácie na Dráhy; prázdna simulácia ukáže návod; „Prispôsobiť“ ignoruje popisy
- [x] Bez čistenia voľnej medi sa ponechaná voľná meď kreslí tlmenou farbou (s popisom)
- [x] Prejazdy v reze (spojenia dráh) sa kreslia prerušovane, s popisom pri myši
- [x] Build presunutý na GitHub Actions (windows-2025, Python 3.13.17, Inno Setup 6.7.1): 91 testov, inštalácia, samotest, HTTPS; 0.3.4 z Actions podpísaná a overená (nezverejnená, zverejnená ostáva zostava z VM)
- [x] VM 7112 zmazaná v Proxmoxe aj s diskami (2026-11-14)
- [ ] Návrh na odstránenie (čaká na súhlas): `private/vm.env`, `private/vmexec.py`, `private/INFRA.md`, csec `z1pcb-win11-z1dev`, 3 riadky 192.168.3.128 v `~/.ssh/known_hosts`
