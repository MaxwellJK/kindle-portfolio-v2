#!/bin/sh

# --- CONFIGURATION ---
SERVER="http://192.168.1.84:8000"
URL="$SERVER/display/image.png"
SAVEPATH="/mnt/us/screensaver/portfolio.png"
TEST_IP="192.168.1.1" # Used to check if internet is actually working
WAIT=300  # 5 min

seconds_to_next_run() {
    now=$(date +%s)
    dow=$(date +%u)    # 1=Mon 7=Sun
    hour=$(date +%H | sed 's/^0//')   # strip leading zero
    min=$(date +%M | sed 's/^0//')
    sec=$(date +%S | sed 's/^0//')

    # Seconds since midnight
    elapsed=$(( hour * 3600 + min * 60 + sec ))

    # Start and end of trading window in seconds since midnight
    start=$(( 8 * 3600 ))
    end=$(( 20 * 3600 ))

    if [ "$dow" -le 5 ] && [ "$elapsed" -ge "$start" ] && [ "$elapsed" -lt "$end" ]; then
        # During trading hours — next 15-min boundary
        slot_sec=$(( (elapsed / 900 + 1) * 900 ))
        echo $(( slot_sec - elapsed ))
    else
        # Outside trading hours — calculate seconds to next weekday 08:00
        secs_today=$(( 86400 - elapsed + start ))
        if [ "$dow" -eq 5 ] && [ "$elapsed" -ge "$end" ]; then
            # Friday after 20:00 — skip to Monday
            echo $(( secs_today + 2 * 86400 ))
        elif [ "$dow" -eq 6 ]; then
            # Saturday — skip to Monday
            echo $(( secs_today + 86400 ))
        elif [ "$dow" -eq 7 ]; then
            # Sunday — skip to Monday
            echo $(( secs_today ))
        else
            # Weekday before 08:00 or after 20:00
            echo $(( secs_today ))
        fi
    fi
}


while true; do
    echo "--- Cycle Started: $(date) ---"

    # 1. Enable Wi-Fi
    lipc-set-prop com.lab126.cmd wirelessEnable 1

    # 2. Wait for Wi-Fi connection (Up to 30 seconds)
    echo "Waiting for Wi-Fi..."
    CONNECTED=0
    for i in $(seq 1 30); do
        if ping -c 1 $TEST_IP >/dev/null 2>&1; then
            echo "Connected!"
            CONNECTED=1
            break
        fi
        sleep 1
    done

    # 3. Download the file if connected
    if [ $CONNECTED -eq 1 ]; then
        echo "Downloading image..."
        wget -q -O "$SAVEPATH" "$URL"
        # touch "$SAVEPATH" # Force filesystem to notice the change
    else
        echo "Wi-Fi timeout. Skipping download."
    fi

    # 4. Disable Wi-Fi immediately to save battery
    lipc-set-prop com.lab126.cmd wirelessEnable 0
    sleep 1

    # # 5. Refresh the screen (Landscape + Dithered)
    # $FBINK -g "$SAVEPATH" -s -d -r 1 -f

    # 6. Calculate seconds to next 8:00 (AM or PM)
    NOW=$(date +%s)
    # T8AM=$(date -d "08:00:00" +%s)
    # T8PM=$(date -d "20:00:00" +%s)

    # if [ "$NOW" -lt "$T8AM" ]; then
    #     WAIT=$((T8AM - NOW))
    # elif [ "$NOW" -lt "$T8PM" ]; then
    #     WAIT=$((T8PM - NOW))
    # else
    #     WAIT=$(( $(date -d "tomorrow 08:00:00" +%s) - NOW ))
    # fi

    # 7. Set Hardware Alarm
    echo "Next refresh in $WAIT seconds. Sleeping now."
    next_run=$(seconds_to_next_run)
    rtcwake -d /dev/rtc0 -m on -s $next_run

    # 8. Force Deep Sleep / Screensaver
    # Note: If powerButton 1 doesn't work, use the 'echo mem' method
    STATE=$(lipc-get-prop com.lab126.powerd state)

    if [ "$STATE" = "active" ]; then
        echo "Kindle is active. Pressing button once to sleep..."
        lipc-set-prop com.lab126.powerd powerButton 1
    elif [ "$STATE" = "screenSaver" ] || [ "$STATE" = "ready_to_suspend" ]; then
        echo "Kindle is in screensaver/idle. Double pressing..."
        lipc-set-prop com.lab126.powerd powerButton 1
        sleep 1
        lipc-set-prop com.lab126.powerd powerButton 1
    else
        echo "Current state is $STATE. No action needed."
    fi

    # The script 'pauses' here during deep sleep. 
    # When it wakes up at 8:00, it resumes and loops.
    sleep 10 
done