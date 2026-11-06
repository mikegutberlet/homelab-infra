# Desktop emulators for PS2 and newer

RomM (https://romm.tail9af84.ts.net) plays everything up to the PS1, N64, DS and PSP in the
browser. PS2, GameCube, Wii, PS3, Xbox, Switch, Wii U and 3DS have no browser core, so those run in
a desktop emulator on your Windows PC or Mac. This kit keeps the installers, the ROMs and the BIOS
files together on the NAS so a new machine is ready in a few minutes.

`playbooks/emulator-kit.yml` fills `NAS/Media/Games/emulators/` (`--check` first; re-run for
newer releases, old installers stay):

```
Media/Games/
  ROMs/<console>/      the games (RomM's library; new folders: GameCube, Wii, WiiU, PS3, Xbox, Switch, 3DS)
  bios/<slug>/         firmware, named the way the emulators expect (rom-organize.yml -e bios_names=true)
  emulators/windows/   installers for Windows
  emulators/mac/       installers for Mac (Intel or universal builds)
  emulators/README.md  this page
  emulators/versions.txt  which release each file is
```

## 1. Mount the Games share

The NAS is `192.168.1.209` (TrueNAS, home network only). The SMB share is `NAS` (the whole
`DeepData/NAS` dataset); log in as the TrueNAS user `storage` (guest is off). Checked 2026-11-05.

**Mac:** Finder > Go > Connect to Server, `smb://storage@192.168.1.209/NAS`, enter the password
and tick "Remember". Games is then `/Volumes/NAS/Media/Games`. To reconnect at login: System
Settings > General > Login Items > add the `NAS` volume.

**Windows:** File Explorer > This PC > Map network drive, drive `G:`, folder
`\\192.168.1.209\NAS`, tick "Reconnect at sign-in" and "Connect using different credentials",
user `storage`. Games is then `G:\Media\Games`.

## 2. Install the emulator you need

Copy the installer from `emulators/windows` or `emulators/mac` to the local disk first (running
an installer straight off a network share is slow and some refuse). Mac: open the `.dmg` or unzip,
drag the app to Applications, and on first launch right-click > Open to pass Gatekeeper.

| Console | Emulator | Needs from the NAS | Notes |
| --- | --- | --- | --- |
| PS2 | PCSX2 | ROMs `PS2`, BIOS `bios/ps2` | Settings > BIOS > directory = `bios/ps2`. Games: Settings > Game List > add `ROMs/PS2`. x64 only on Mac |
| GameCube, Wii | Dolphin | ROMs `GameCube`, `Wii` | No BIOS needed. Config > Paths > add both folders. Wii remote: a real one over Bluetooth, or emulate with a controller |
| PS3 | RPCS3 | ROMs `PS3` | Needs PS3 firmware: download `PS3UPDAT.PUP` from Sony's site (free), File > Install Firmware. Games: File > Add Games Folder |
| Xbox | xemu | ROMs `Xbox`, BIOS `bios/xbox` | Needs an MCPX boot ROM, a BIOS and an HDD image; point Machine > Settings at `bios/xbox` once those files are there |
| Switch | Ryujinx (community build) | ROMs `Switch` | Needs `prod.keys` and firmware dumped from your own Switch (File > Open Ryujinx Folder > `system`). Games: Options > Settings > General > add `ROMs/Switch` |
| Wii U | Cemu | ROMs `WiiU` | Needs `keys.txt` from your own console for most titles. Options > General > Game Paths |
| 3DS | Azahar | ROMs `3DS` | No BIOS needed. File > Add New Game Directory |
| Everything older | OpenEmu (Mac) | ROMs, `bios/<slug>` | Optional on the Mac: one app for GB to PSP with a console-style library. Windows users have the browser via RomM |

Keep save files on the local disk (the emulator defaults). Saving straight onto the share works
but a dropped Wi-Fi link mid-save corrupts the file.

## 3. Playing over Tailscale

Away from home, a mounted share streams the whole ISO while you play. PS2 games are 1 to 4 GB,
GameCube 1.4 GB, Wii up to 8 GB, so copy the game to the local disk first when you are not on the
home network. RomM's Download button on a game page does the same copy through the browser.

## Keeping the kit current

```
cd ~/HomeLab/homelab-infra/ansible && ~/.local/share/homelab-ansible/bin/ansible-playbook playbooks/emulator-kit.yml --check
cd ~/HomeLab/homelab-infra/ansible && ~/.local/share/homelab-ansible/bin/ansible-playbook playbooks/emulator-kit.yml
```

The script asks each project's GitHub release page for the newest build, so a run after a new
release downloads it next to the old one; `versions.txt` names the current file. If a project
changes its file names the run says "no asset matches" and the installer comes from the project
site instead (pcsx2.net, dolphin-emu.org, rpcs3.net, xemu.app, ryujinx.app, cemu.info,
azahar-emu.org, openemu.org).
