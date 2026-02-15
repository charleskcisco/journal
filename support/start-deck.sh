#!/bin/bash

# The '--' is the critical part here
cage foot -- sh -c "cd /path/to/journal && ./run.sh; exec bash" &

# Keep your existing wait loop
for i in {1..50}; do
    if [ -S "$WAYLAND_DISPLAY" ] || [ -S "/run/user/$(id -u)/wayland-0" ]; then
        break
    fi
    sleep 0.1
done

# Your confirmed rotation---you'll have to install wlr-randr for this to work
sleep 0.5
wlr-randr --output HDMI-A-1 --transform 90

wait $!
