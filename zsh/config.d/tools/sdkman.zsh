# SDKMAN — JVM ecosystem version manager
#
# Manages Java, Kotlin, Gradle, Maven, Scala, and other JVM-platform tools.
# No-op if SDKMAN is not installed.
#
# If you use mise instead of SDKMAN, disable this snippet.
#
# Install:         https://sdkman.io/install
# Docs:            https://sdkman.io/usage
# duplicate-check: sdkman-init\.sh

export SDKMAN_DIR="$HOME/.sdkman"

[[ -s "$SDKMAN_DIR/bin/sdkman-init.sh" ]] || return 0

source "$SDKMAN_DIR/bin/sdkman-init.sh"
