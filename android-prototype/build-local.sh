#!/bin/bash
set -euo pipefail
project_dir="$(cd "$(dirname "$0")" && pwd)"
tools_dir="$(cd "$project_dir/../.." && pwd)/.android-build-tools"
export JAVA_HOME="$tools_dir/jdk/Contents/Home"
export ANDROID_HOME="$tools_dir/sdk"
export GRADLE_USER_HOME="$tools_dir/gradle-cache"
exec "$tools_dir/gradle-8.9/bin/gradle" -p "$project_dir" --no-daemon assembleDebug "$@"
