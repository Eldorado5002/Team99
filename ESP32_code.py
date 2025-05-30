import time
from machine import Pin, I2C, PWM
from ssd1306 import SSD1306_I2C
import network
from umqtt.robust import MQTTClient
import ubinascii

# WiFi Credentials
SSID = "Your_WiFi_Name"
PASSWORD = "Your_WiFi_Password"

# MQTT Broker Details
MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT = 1883
CLIENT_ID = "ESP32_Parking_" + ubinascii.hexlify(network.WLAN().config('mac')).decode()
TOPIC_PREFIX = "parking_system_custom_123456/"
TOPIC_PUB_SLOT = TOPIC_PREFIX + "slot_status"
TOPIC_PUB_GATE_STATUS = TOPIC_PREFIX + "gate_status"
TOPIC_SUB_VEHICLE = TOPIC_PREFIX + "vehicle_status"
TOPIC_SUB_GATE = TOPIC_PREFIX + "gate_control"

# Hardware Setup
i2c = I2C(0, scl=Pin(5), sda=Pin(4), freq=400000)
oled = SSD1306_I2C(128, 64, i2c)

entry_red_led = Pin(19, Pin.OUT)
entry_yellow_led = Pin(25, Pin.OUT)
entry_green_led = Pin(26, Pin.OUT)

exit_red_led = Pin(27, Pin.OUT)
exit_yellow_led = Pin(32, Pin.OUT)
exit_green_led = Pin(33, Pin.OUT)

buzzer = PWM(Pin(12))
buzzer.freq(1000)
buzzer.duty(0)

exit_ir_sensor = Pin(14, Pin.IN)

slot1_ir_sensor = Pin(34, Pin.IN)
slot2_ir_sensor = Pin(35, Pin.IN)
slot3_ir_sensor = Pin(36, Pin.IN)
slot4_ir_sensor = Pin(39, Pin.IN)
slot5_ir_sensor = Pin(16, Pin.IN)
slot6_ir_sensor = Pin(17, Pin.IN)

entry_servo = PWM(Pin(18))
entry_servo.freq(50)
entry_servo.duty(26)

exit_servo = PWM(Pin(15))
exit_servo.freq(50)
exit_servo.duty(26)

# Initialize all LEDs to OFF
entry_red_led.value(0)
entry_yellow_led.value(0)
entry_green_led.value(0)
exit_red_led.value(0)
exit_yellow_led.value(0)
exit_green_led.value(0)

parking_slots = [False, False, False, False, False, False]

# MQTT Variables
mqtt_connected = False
client = None
last_connection_check = 0

# LED State Variables (NEW - to prevent flickering)
current_entry_led_state = "NONE"
current_exit_led_state = "NONE"
last_display_update = 0
last_slot_check = 0

# MQTT Functions
def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print("Connecting to WiFi...")
        wlan.connect(SSID, PASSWORD)
        max_wait = 20
        while max_wait > 0 and not wlan.isconnected():
            time.sleep(1)
            max_wait -= 1
    if wlan.isconnected():
        print("WiFi Connected:", wlan.ifconfig())
        return True
    else:
        print("WiFi Connection Failed")
        return False

def connect_mqtt():
    global client, mqtt_connected
    try:
        print("Connecting to MQTT Broker:", MQTT_BROKER)
        client = MQTTClient(CLIENT_ID, MQTT_BROKER, MQTT_PORT)
        client.set_callback(mqtt_callback)
        client.connect()
        print("Connected to MQTT Broker")
        client.subscribe(TOPIC_SUB_GATE)
        client.subscribe(TOPIC_SUB_VEHICLE)
        print("Subscribed to topics")
        mqtt_connected = True
        publish_message(TOPIC_PREFIX + "device_status", "ONLINE")
        return client
    except Exception as e:
        print("MQTT Connection Failed:", e)
        mqtt_connected = False
        return None

def publish_message(topic, message):
    global mqtt_connected, client
    try:
        if mqtt_connected:
            client.publish(topic, message)
            return True
    except Exception as e:
        print("Publish Error:", e)
        mqtt_connected = False
    return False

def mqtt_callback(topic, msg):
    global entry_state, entry_timer, entry_gate_open
    try:
        t = topic.decode()
        m = msg.decode()
        print("MQTT Message:", t, "->", m)
        if t == TOPIC_SUB_GATE:
            if m == "OPEN" and get_available_slots() > 0:
                entry_state = "GREEN"
                entry_timer = time.ticks_ms()
            elif m == "CLOSE":
                entry_state = "YELLOW"
                entry_timer = time.ticks_ms()
        elif t == TOPIC_SUB_VEHICLE:
            if m == "DETECTED" and get_available_slots() > 0:
                entry_state = "GREEN"
                entry_timer = time.ticks_ms()
    except Exception as e:
        print("Error in callback:", e)

def reconnect_if_needed():
    global client, mqtt_connected, last_connection_check
    current_time = time.time()
    if current_time - last_connection_check > 30:
        last_connection_check = current_time
        if not mqtt_connected:
            if not network.WLAN(network.STA_IF).isconnected():
                connect_wifi()
            client = connect_mqtt()

# IMPROVED LED Control Functions
def set_entry_leds(state):
    global current_entry_led_state
    if current_entry_led_state != state:  # Only change if different
        current_entry_led_state = state
        # Turn off all LEDs first
        entry_red_led.value(0)
        entry_yellow_led.value(0)
        entry_green_led.value(0)
        time.sleep(0.01)  # Small delay to ensure LEDs are off
        
        # Turn on the required LED
        if state == "RED":
            entry_red_led.value(1)
        elif state == "YELLOW":
            entry_yellow_led.value(1)
        elif state == "GREEN":
            entry_green_led.value(1)
        print(f"Entry LED set to: {state}")

def set_exit_leds(state):
    global current_exit_led_state
    if current_exit_led_state != state:  # Only change if different
        current_exit_led_state = state
        # Turn off all LEDs first
        exit_red_led.value(0)
        exit_yellow_led.value(0)
        exit_green_led.value(0)
        time.sleep(0.01)  # Small delay to ensure LEDs are off
        
        # Turn on the required LED
        if state == "RED":
            exit_red_led.value(1)
        elif state == "YELLOW":
            exit_yellow_led.value(1)
        elif state == "GREEN":
            exit_green_led.value(1)
        print(f"Exit LED set to: {state}")

def entry_loud_beep():
    buzzer.duty(512)  # Reduced duty cycle for better sound
    time.sleep(0.3)
    buzzer.duty(0)

def exit_loud_beep():
    buzzer.duty(512)  # Reduced duty cycle for better sound
    time.sleep(0.2)
    buzzer.duty(0)
    time.sleep(0.1)
    buzzer.duty(512)
    time.sleep(0.2)
    buzzer.duty(0)

def entry_open_gate():
    entry_servo.duty(77)
    print("Entry gate opened")

def entry_close_gate():
    entry_servo.duty(26)
    print("Entry gate closed")

def exit_open_gate():
    exit_servo.duty(77)
    print("Exit gate opened")

def exit_close_gate():
    exit_servo.duty(26)
    print("Exit gate closed")

def get_available_slots():
    return sum(1 for slot in parking_slots if not slot)

def update_parking_slots():
    global last_slot_check
    current_time = time.ticks_ms()
    
    # Only check slots every 100ms to reduce processing
    if time.ticks_diff(current_time, last_slot_check) >= 100:
        last_slot_check = current_time
        old_slots = parking_slots.copy()
        
        slots = [slot1_ir_sensor.value() == 0, slot2_ir_sensor.value() == 0, slot3_ir_sensor.value() == 0,
                 slot4_ir_sensor.value() == 0, slot5_ir_sensor.value() == 0, slot6_ir_sensor.value() == 0]
        
        for i in range(6):
            parking_slots[i] = slots[i]
        
        # Only update display if slots changed
        if old_slots != parking_slots:
            print("Parking slots updated:", parking_slots)

def display_parking_status():
    global last_display_update
    current_time = time.ticks_ms()
    
    # Only update display every 500ms to prevent flickering
    if time.ticks_diff(current_time, last_display_update) >= 500:
        last_display_update = current_time
        
        oled.fill(0)
        oled.text("Parking System", 5, 0)
        available = get_available_slots()
        oled.text(f"Available: {available}/6", 5, 15)
        
        for i in range(6):
            status = "X" if parking_slots[i] else "O"
            row = 25 + (i // 3) * 15
            col = 10 + (i % 3) * 35
            oled.text(f"S{i+1}:{status}", col, row)
        
        oled.show()
        
        # Publish MQTT status
        status = ",".join([str(i+1) for i, slot in enumerate(parking_slots) if not slot]) if available else "FULL"
        publish_message(TOPIC_PUB_SLOT, status)

# Initialize System
oled.fill(0)
oled.text("Parking System", 10, 20)
oled.text("Starting...", 30, 40)
oled.show()
time.sleep(2)

entry_state = "RED"
entry_timer = time.ticks_ms()
entry_gate_open = False

exit_state = "RED"
exit_timer = time.ticks_ms()
exit_gate_open = False

# Initialize WiFi and MQTT
if connect_wifi():
    client = connect_mqtt()
else:
    client = None

# Set initial LED states
set_entry_leds("RED")
set_exit_leds("RED")

print("System initialized. Starting main loop...")

# Main Loop
while True:
    current_time = time.ticks_ms()

    # MQTT handling (every loop)
    reconnect_if_needed()
    if mqtt_connected:
        try:
            client.check_msg()
        except Exception as e:
            print("Error checking messages:", e)
            mqtt_connected = False

    # Update parking slots
    update_parking_slots()
    display_parking_status()

    # Entry Gate Logic
    if entry_state == "RED":
        set_entry_leds("RED")

    elif entry_state == "GREEN":
        set_entry_leds("GREEN")
        if not entry_gate_open:
            entry_open_gate()
            entry_loud_beep()
            entry_gate_open = True
            # Assign parking slot
            for i in range(6):
                if not parking_slots[i]:
                    parking_slots[i] = True
                    break
        
        # Auto close after 3 seconds
        if time.ticks_diff(current_time, entry_timer) >= 3000:
            entry_state = "YELLOW"
            entry_timer = current_time
            entry_close_gate()
            entry_gate_open = False
            publish_message(TOPIC_PUB_GATE_STATUS, "CLOSED")

    elif entry_state == "YELLOW":
        set_entry_leds("YELLOW")
        # Stay yellow for 2 seconds before going red
        if time.ticks_diff(current_time, entry_timer) >= 2000:
            entry_state = "RED"
            entry_timer = current_time

    # Exit Gate Logic
    if exit_state == "RED":
        set_exit_leds("RED")
        # Check for vehicle at exit
        if exit_ir_sensor.value() == 0:
            exit_state = "GREEN"
            exit_timer = current_time

    elif exit_state == "GREEN":
        set_exit_leds("GREEN")
        if not exit_gate_open:
            exit_open_gate()
            exit_loud_beep()
            exit_gate_open = True
            # Free up parking slot
            for i in range(5, -1, -1):
                if parking_slots[i]:
                    parking_slots[i] = False
                    break
        
        # Auto close after 3 seconds
        if time.ticks_diff(current_time, exit_timer) >= 3000:
            exit_state = "YELLOW"
            exit_timer = current_time
            exit_close_gate()
            exit_gate_open = False

    elif exit_state == "YELLOW":
        set_exit_leds("YELLOW")
        # Stay yellow for 2 seconds before going red
        if time.ticks_diff(current_time, exit_timer) >= 2000:
            exit_state = "RED"
            exit_timer = current_time

    # Reduced loop delay
    time.sleep(0.05)  