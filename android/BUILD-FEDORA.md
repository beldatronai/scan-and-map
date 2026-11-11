# Build and install from Fedora

```bash
# 1. JDK 17 and adb
sudo dnf install java-17-openjdk-devel android-tools

# 2. Android command-line tools (SDK)
mkdir -p ~/Android/Sdk/cmdline-tools && cd ~/Android/Sdk/cmdline-tools
curl -LO https://dl.google.com/android/repository/commandlinetools-linux-13114758_latest.zip   # or the newest from developer.android.com/studio#command-tools
unzip commandlinetools-linux-*_latest.zip && mv cmdline-tools latest
export ANDROID_HOME=~/Android/Sdk
export PATH=$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$PATH
export JAVA_HOME=/usr/lib/jvm/java-17-openjdk
yes | sdkmanager --licenses
sdkmanager "platform-tools" "platforms;android-36" "build-tools;36.0.0"

# 3. Build
cd ~/scan-and-map/android
./gradlew assembleLanDebug           # -> app/build/outputs/apk/lan/debug/app-lan-debug.apk
./gradlew assembleOfflineDebug       # -> app/build/outputs/apk/offline/debug/app-offline-debug.apk (no network permission)

# 4. Install over USB (enable Developer options + USB debugging on the phone first)
adb devices                    # accept the "Allow USB debugging" prompt on the phone
adb install -r app/build/outputs/apk/offline/debug/app-offline-debug.apk   # or the lan APK
```

Put the three `export` lines in `~/.bashrc` to keep them. Android Studio (Flatpak `com.google.AndroidStudio`)
works too: open the `android/` folder and press Run.

If `adb devices` shows `no permissions`, Fedora's `android-tools` package ships the udev rules; unplug and
replug the phone, or run `sudo udevadm control --reload-rules` and replug.

Wireless instead of USB (Android 11+): Developer options → Wireless debugging → Pair device with pairing
code, then `adb pair <ip>:<port>` and `adb connect <ip>:<port>`. On Android 10 (Galaxy A6): connect USB once,
run `adb tcpip 5555`, unplug, then `adb connect <phone-ip>:5555`.

Pulling a session without the upload button:

```bash
adb pull /sdcard/Android/data/dev.scanmap.capture/files/sessions/ ~/scanmap/sessions/
```
