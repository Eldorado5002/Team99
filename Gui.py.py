import cv2
from time import time
import numpy as np
from ultralytics import YOLO
from ultralytics.utils.plotting import Annotator, colors
from datetime import datetime
from paddleocr import PaddleOCR
from pymongo import MongoClient
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk
import re
import paho.mqtt.client as mqtt
import threading

class SpeedEstimator:
    def __init__(self):
        # Load YOLOv8m model for vehicle detection
        self.model = YOLO("yolov8m.pt")
        self.spd = {}
        self.trk_pt = {}
        self.trk_pp = {}
        self.logged_ids = set()
        self.new_detections = []  # For GUI updates
        self.ocr = PaddleOCR(use_angle_cls=True, lang='en')
        self.db_connection = self.connect_to_db()
        self.db_connected = self.db_connection is not None
        self.car_pattern = re.compile(r'^[A-Z]{2}\d{2}[A-Z]{2}\d{4}$')
        self.bike_pattern = re.compile(r'^[A-Z]{2}\d{2}[A-Z]{1,2}\d{4}$')
        self.mqtt_client = self.setup_mqtt()
        self.TOPIC_PREFIX = "parking_system_custom_123456/"
        self.TOPIC_SUB_GATE = self.TOPIC_PREFIX + "gate_control"
        self.TOPIC_SUB_GATE_STATUS = self.TOPIC_PREFIX + "gate_status"
        self.TOPIC_PUB_VEHICLE = self.TOPIC_PREFIX + "vehicle_status"
        self.mqtt_status = "Disconnected"
        # REMOVED: self.last_vehicle_detection_time = 0
        # REMOVED: self.vehicle_detection_cooldown = 2
        self.gui_callback = None  # Callback for GUI updates
        self.gate_status = "Unknown"
        self.detection_counter = 0  # Counter for all detections

    def set_gui_callback(self, callback):
        """Set callback function for GUI updates"""
        self.gui_callback = callback

    def setup_mqtt(self):
        """Initialize MQTT client and connect to broker."""
        try:
            client_id = f"SpeedEstimator_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            client = mqtt.Client(client_id)
            client.on_connect = self.on_mqtt_connect
            client.on_disconnect = self.on_disconnect
            client.on_message = self.on_message
            client.connect("broker.hivemq.com", 1883, 60)
            client.loop_start()
            self.mqtt_status = "Connected"
            return client
        except Exception as err:
            self.mqtt_status = f"Error: {str(err)[:20]}"
            return None

    def on_mqtt_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.mqtt_status = "Connected"
            # Subscribe to both topics
            client.subscribe(self.TOPIC_SUB_GATE_STATUS)
            client.subscribe(self.TOPIC_SUB_GATE)
            print(f"Subscribed to: {self.TOPIC_SUB_GATE_STATUS}")
            print(f"Subscribed to: {self.TOPIC_SUB_GATE}")
        else:
            self.mqtt_status = f"Failed: {rc}"

    def on_disconnect(self, client, userdata, rc):
        self.mqtt_status = "Disconnected"
        if rc != 0:
            try:
                client.reconnect()
            except:
                pass

    def on_message(self, client, userdata, msg):
        """Handle incoming MQTT messages"""
        try:
            topic = msg.topic
            message = msg.payload.decode('utf-8')
            print(f"Received MQTT message - Topic: {topic}, Message: {message}")
            
            if topic == self.TOPIC_SUB_GATE_STATUS:
                self.gate_status = message
                if self.gui_callback:
                    self.gui_callback("gate_status", message)
            elif topic == self.TOPIC_SUB_GATE:
                if self.gui_callback:
                    self.gui_callback("gate_control", message)
        except Exception as e:
            print(f"Error processing MQTT message: {e}")

    def send_gate_open_signal(self):
        """Send gate open signal - ALWAYS executes without cooldown"""
        if self.mqtt_client is not None:
            try:
                # Send vehicle detection signal
                self.mqtt_client.publish(self.TOPIC_PUB_VEHICLE, "DETECTED")
                print(f"Published to {self.TOPIC_PUB_VEHICLE}: DETECTED")
                
                # Send gate open command IMMEDIATELY
                self.mqtt_client.publish(self.TOPIC_SUB_GATE, "OPEN")
                print(f"Published to {self.TOPIC_SUB_GATE}: OPEN")
                
                # REMOVED: self.last_vehicle_detection_time = time()
                
                # Notify GUI about vehicle detection and gate command
                if self.gui_callback:
                    self.gui_callback("vehicle_detected", "🚗 Vehicle Detected - Gate Opening!")
                    self.gui_callback("gate_command", "🚪 OPEN Command Sent Immediately")
                
                return True
            except Exception as err:
                self.mqtt_status = f"Send Error: {str(err)[:20]}"
                print(f"MQTT publish error: {err}")
                return False
        else:
            print("MQTT client not connected - cannot send gate signal")
        return False

    def connect_to_db(self):
        try:
            client = MongoClient('mongodb://localhost:27017/')
            db = client['toycartest']
            collection = db['my_data']
            # Try a simple command to check connection
            client.admin.command('ping')
            print("Connected to MongoDB")
            return collection
        except Exception as err:
            print(f"Database connection error: {err}")
            return None

    def preprocess_roi(self, roi):
        """Preprocess ROI for OCR (grayscale, histogram equalization, sharpening)."""
        if roi is None or not isinstance(roi, np.ndarray):
            return None
        # Convert to grayscale
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        # Apply histogram equalization
        equalized = cv2.equalizeHist(gray)
        # Apply sharpening filter
        kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
        sharpened = cv2.filter2D(equalized, -1, kernel)
        # Convert back to 3-channel for PaddleOCR compatibility
        return cv2.cvtColor(sharpened, cv2.COLOR_GRAY2BGR)

    def perform_ocr(self, image_array):
        if image_array is None or not isinstance(image_array, np.ndarray):
            return ""
        results = self.ocr.ocr(image_array, rec=True)
        text = ' '.join([result[1][0] for result in results[0]] if results[0] else "")
        clean_text = ''.join(char for char in text if char.isalnum()).upper()
        if self.car_pattern.match(clean_text) or self.bike_pattern.match(clean_text):
            return clean_text
        return ""

    def save_to_database(self, date, time_str, track_id, class_name, speed, numberplate):
        """Save to database - REMOVED cooldown check"""
        try:
            document = {
                'date': date,
                'time': time_str,
                'track_id': track_id,
                'class_name': class_name,
                'speed': speed,
                'numberplate': numberplate,
                'vehicle_type': 'car' if self.car_pattern.match(numberplate) else 'bike'
            }
            if self.db_connection is not None:
                self.db_connection.insert_one(document)
            
            # Send MQTT signals - ALWAYS
            gate_opened = self.send_gate_open_signal()
            
            self.new_detections.append({
                'time': time_str,
                'track_id': track_id,
                'speed': speed,
                'numberplate': numberplate,
                'vehicle_type': class_name,
                'gate_status': 'OPENED' if gate_opened else 'ERROR'
            })
            return True
        except Exception as e:
            print(f"Database save error: {e}")
            # Still try to send gate signal even if DB fails
            self.send_gate_open_signal()
            return True

    def estimate_speed(self, im0):
        """Detect vehicles, estimate speed, and extract number plates."""
        annotator = Annotator(im0, line_width=2)
        # Use original BGR frame for YOLOv8 detection
        results = self.model.track(im0, persist=True, conf=0.25, iou=0.45, classes=[2, 3, 5, 7])  # car, motorcycle, bus, truck
        current_time = datetime.now()

        if results[0].boxes is not None and results[0].boxes.id is not None:
            boxes = results[0].boxes.xyxy.cpu().numpy()
            track_ids = results[0].boxes.id.cpu().numpy().astype(int)
            classes = results[0].boxes.cls.cpu().numpy()

            for box, track_id, cls in zip(boxes, track_ids, classes):
                x1, y1, x2, y2 = map(int, box)
                if track_id not in self.trk_pt:
                    self.trk_pt[track_id] = time()
                    self.trk_pp[track_id] = (x1, y1)
                
                time_diff = time() - self.trk_pt[track_id]
                dist = np.linalg.norm(np.array(self.trk_pp[track_id]) - np.array((x1, y1)))
                speed = (dist / time_diff) * 3.6 if time_diff > 0 else 0
                self.spd[track_id] = round(speed, 2)
                self.trk_pt[track_id] = time()
                self.trk_pp[track_id] = (x1, y1)

                # Include vehicle type in bounding box label
                class_name = self.model.names[int(cls)]
                label = f"ID: {track_id} {class_name} {self.spd[track_id]} km/h"
                annotator.box_label(box, label=label, color=colors(track_id % 80, True))

                # ALWAYS SEND GATE SIGNAL - No cooldown, no uniqueness check
                # Every single detection frame triggers gate opening
                self.detection_counter += 1
                gate_opened = self.send_gate_open_signal()
                
                # Log every detection
                detection_record = {
                    'time': current_time.strftime("%H:%M:%S"),
                    'track_id': track_id,
                    'speed': self.spd[track_id],
                    'numberplate': 'Processing...',
                    'vehicle_type': class_name,
                    'gate_status': 'OPENED' if gate_opened else 'ERROR',
                    'detection_count': self.detection_counter
                }
                self.new_detections.append(detection_record)
                
                # Show gate status on video for EVERY detection
                gate_status = "GATE OPENING" if gate_opened else "GATE ERROR"
                detection_label = f"VEHICLE DETECTED #{self.detection_counter} - {gate_status}"
                cv2.putText(
                    im0,
                    detection_label,
                    (x1, y1 - 50),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 255, 0) if gate_opened else (0, 0, 255),
                    2
                )

                # Continue with OCR processing for logging
                if x2 > x1 and y2 > y1:
                    roi = im0[y1:y2, x1:x2]
                    preprocessed_roi = self.preprocess_roi(roi)
                    ocr_text = self.perform_ocr(preprocessed_roi)
                    if ocr_text.strip():
                        # Update database with OCR results if available
                        try:
                            if self.db_connection is not None:
                                document = {
                                    'date': current_time.strftime("%Y-%m-%d"),
                                    'time': current_time.strftime("%H:%M:%S"),
                                    'track_id': track_id,
                                    'class_name': class_name,
                                    'speed': self.spd[track_id],
                                    'numberplate': ocr_text,
                                    'vehicle_type': 'car' if self.car_pattern.match(ocr_text) else 'bike',
                                    'detection_count': self.detection_counter
                                }
                                self.db_connection.insert_one(document)
                        except Exception as e:
                            print(f"Database update error: {e}")
                        
                        vehicle_type = 'car' if self.car_pattern.match(ocr_text) else 'bike'
                        plate_label = f"{ocr_text} ({vehicle_type})"
                        cv2.putText(
                            im0,
                            plate_label,
                            (x1, y1 - 30),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.5,
                            (0, 255, 255),
                            2
                        )
        return im0

    def cleanup(self):
        if self.mqtt_client is not None:
            try:
                self.mqtt_client.publish(self.TOPIC_PUB_VEHICLE, "NONE")
                time.sleep(0.5)
                self.mqtt_client.loop_stop()
                self.mqtt_client.disconnect()
            except Exception:
                pass

class ParkingSystemGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Parking System - Entry Monitoring (Always Open Gate)")
        self.root.geometry("1280x720")
        self.root.configure(bg="#0f1419")
        self.cap = None
        self.is_running = False
        self.speed_estimator = SpeedEstimator()
        self.speed_estimator.set_gui_callback(self.handle_mqtt_callback)
        self.notification_queue = []  # Queue for notifications
        self.setup_gui()

    def handle_mqtt_callback(self, event_type, message):
        """Handle callbacks from SpeedEstimator for GUI updates"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        if event_type == "vehicle_detected":
            self.show_notification(f"🚗 {message}", "success")
            self.update_gate_status("Vehicle Detected - Gate Opening")
        elif event_type == "gate_command":
            self.show_notification(f"🚪 {message}", "info")
        elif event_type == "gate_status":
            self.update_gate_status(f"Gate: {message}")
            if message.upper() in ["OPEN", "OPENED"]:
                self.show_notification("🟢 Gate Opened", "success")
            elif message.upper() in ["CLOSED", "CLOSE"]:
                self.show_notification("🔴 Gate Closed", "warning")
        elif event_type == "gate_control":
            if message.upper() == "OPEN":
                self.show_notification("📤 Gate Open Command Received", "info")

    def show_notification(self, message, notification_type="info"):
        """Show a temporary notification in the GUI"""
        # Create notification frame
        notification = tk.Frame(self.main_content, bg="#1e293b", relief="solid", bd=1)
        notification.place(relx=0.02, rely=0.02, relwidth=0.4)
        
        # Color coding based on type
        colors = {
            "success": "#10b981",
            "warning": "#f59e0b", 
            "error": "#ef4444",
            "info": "#3b82f6"
        }
        
        color = colors.get(notification_type, "#3b82f6")
        
        # Notification content
        tk.Label(notification, text=message, bg="#1e293b", fg=color, 
                font=("Segoe UI", 11, "bold")).pack(pady=8, padx=10)
        
        # Auto-hide after 2 seconds (reduced from 3 to handle frequent notifications)
        self.root.after(2000, lambda: notification.destroy())

    def update_gate_status(self, status):
        """Update gate status in the status panel"""
        self.status_vars["Gate"].set(status)
        # Update color based on status
        if "Open" in status or "Detected" in status:
            self.status_labels_widgets["Gate"].configure(foreground="#10b981")  # green
        elif "Closed" in status:
            self.status_labels_widgets["Gate"].configure(foreground="#ef4444")  # red
        else:
            self.status_labels_widgets["Gate"].configure(foreground="#f59e0b")  # yellow

    def setup_gui(self):
        """Set up the GUI layout and components."""
        style = ttk.Style()
        style.configure("TButton", font=("Segoe UI", 12), padding=10)
        style.configure("TLabel", font=("Segoe UI", 12), background="#0f1419", foreground="white")
        style.configure("Status.TLabel", font=("Segoe UI", 10, "bold"))
        style.configure("Title.TLabel", font=("Segoe UI", 14, "bold"), foreground="#93c5fd")

        self.container = tk.Frame(self.root, bg="#0f1419")
        self.container.grid(row=0, column=0, sticky="nsew", padx=20, pady=20)
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        # Header
        self.header = tk.Frame(self.container, bg="#1e293b", bd=1, relief="solid")
        self.header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 20))
        logo_frame = tk.Frame(self.header, bg="#1e293b")
        logo_frame.pack(side="left", padx=30, pady=10)
        tk.Label(logo_frame, text="🚗", font=("Segoe UI", 24), bg="#3b82f6", fg="white", width=3, height=1).pack(side="left", padx=5)
        tk.Label(logo_frame, text="Entry Monitor - Always Open Mode", font=("Segoe UI", 16, "bold"), bg="#1e293b", fg="#93c5fd").pack(side="left")
        control_frame = tk.Frame(self.header, bg="#1e293b")
        control_frame.pack(side="right", padx=30)
        self.start_btn = ttk.Button(control_frame, text="Start System", command=self.start_camera)
        self.start_btn.pack(side="left", padx=5)
        self.stop_btn = ttk.Button(control_frame, text="Stop System", command=self.stop_camera, state="disabled")
        self.stop_btn.pack(side="left", padx=5)

        # Main content and sidebar
        self.main_content = tk.Frame(self.container, bg="#1e293b", bd=1, relief="solid")
        self.main_content.grid(row=1, column=0, sticky="nsew", padx=(0, 20))
        self.sidebar = tk.Frame(self.container, bg="#0f1419", width=350)
        self.sidebar.grid(row=1, column=1, sticky="ns")
        self.container.grid_rowconfigure(1, weight=1)
        self.container.grid_columnconfigure(0, weight=3)
        self.container.grid_columnconfigure(1, weight=1)

        # Camera section
        camera_section = tk.Frame(self.main_content, bg="#1e293b")
        camera_section.pack(fill="both", expand=True, padx=25, pady=25)
        camera_header = tk.Frame(camera_section, bg="#1e293b")
        camera_header.pack(fill="x")
        ttk.Label(camera_header, text="Live Camera Feed", style="Title.TLabel").pack(side="left")
        self.camera_indicator = tk.Label(camera_header, text="●", fg="#ef4444", bg="#1e293b", font=("Segoe UI", 12))
        self.camera_indicator.pack(side="left", padx=10)
        self.camera_resolution = ttk.Label(camera_header, text="Waiting for camera...")
        self.camera_resolution.pack(side="right")
        self.camera_feed = tk.Label(camera_section, text="Camera Feed Offline\nClick 'Start System' to begin monitoring\n\n*** ALWAYS OPEN MODE ***\nGate will open for EVERY vehicle detection",
                                    bg="#1e293b", fg="gray", font=("Segoe UI", 14))
        self.camera_feed.pack(fill="both", expand=True, pady=20)

        # Sidebar: System Status
        self.status_panel = tk.Frame(self.sidebar, bg="#1e293b", bd=1, relief="solid")
        self.status_panel.pack(fill="x", padx=10, pady=(0, 20))
        ttk.Label(self.status_panel, text="📊 System Status", style="Title.TLabel").pack(pady=10)
        self.status_grid = tk.Frame(self.status_panel, bg="#1e293b")
        self.status_grid.pack(fill="x", padx=10, pady=5)
        status_labels = ["Camera", "MQTT", "Database", "Gate", "Total Detections"]
        self.status_vars = {}
        self.status_labels_widgets = {}
        for i, label in enumerate(status_labels):
            ttk.Label(self.status_grid, text=label).grid(row=i, column=0, sticky="w", padx=5, pady=5)
            var = tk.StringVar(value="Disconnected" if label != "Total Detections" else "0")
            self.status_vars[label] = var
            status_label = ttk.Label(self.status_grid, textvariable=var, style="Status.TLabel")
            status_label.grid(row=i, column=1, sticky="e", padx=5, pady=5)
            self.status_labels_widgets[label] = status_label
            if label != "Total Detections":
                status_label.configure(foreground="#ef4444")
            self.status_grid.grid_columnconfigure(1, weight=1)

        # Sidebar: Vehicle Log
        self.vehicle_panel = tk.Frame(self.sidebar, bg="#1e293b", bd=1, relief="solid")
        self.vehicle_panel.pack(fill="both", expand=True, padx=10)
        ttk.Label(self.vehicle_panel, text="🚘 All Detections (Always Open)", style="Title.TLabel").pack(pady=10)
        self.vehicle_log = tk.Frame(self.vehicle_panel, bg="#1e293b")
        self.vehicle_log.pack(fill="both", expand=True, padx=10, pady=5)
        scrollbar = tk.Scrollbar(self.vehicle_log)
        scrollbar.pack(side="right", fill="y")
        self.vehicle_log_text = tk.Text(self.vehicle_log, bg="#1e293b", fg="white", font=("Segoe UI", 10),
                                        yscrollcommand=scrollbar.set, state="disabled")
        self.vehicle_log_text.pack(fill="both", expand=True)
        scrollbar.config(command=self.vehicle_log_text.yview)

        # Footer
        self.footer = tk.Frame(self.container, bg="#1e293b", bd=1, relief="solid")
        self.footer.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(20, 0))
        footer_left = tk.Frame(self.footer, bg="#1e293b")
        footer_left.pack(side="left", padx=30)
        self.system_time = tk.StringVar(value="Loading...")
        ttk.Label(footer_left, textvariable=self.system_time, style="Title.TLabel").pack(side="left")
        self.socket_indicator = tk.Label(footer_left, text="●", fg="#ef4444", bg="#1e293b", font=("Segoe UI", 12))
        self.socket_indicator.pack(side="left", padx=10)
        self.socket_status = tk.StringVar(value="Connecting...")
        ttk.Label(footer_left, textvariable=self.socket_status).pack(side="left")
        tk.Label(self.footer, text="Entry Monitoring System v2.1 - Always Open Mode", bg="#1e293b", fg="white").pack(side="right", padx=30)

        self.update_time()
        self.update_indicators()

    def update_time(self):
        """Update system time every second."""
        self.system_time.set(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self.status_vars["MQTT"].set(self.speed_estimator.mqtt_status)
        
        # Database connection status update and color
        if self.speed_estimator.db_connection is not None:
            self.status_vars["Database"].set("Connected")
            self.status_labels_widgets["Database"].configure(foreground="#10b981")  # green
        else:
            self.status_vars["Database"].set("Disconnected")
            self.status_labels_widgets["Database"].configure(foreground="#ef4444")  # red
        
        # MQTT connection status color
        if self.speed_estimator.mqtt_status == "Connected":
            self.status_labels_widgets["MQTT"].configure(foreground="#10b981")  # green
        else:
            self.status_labels_widgets["MQTT"].configure(foreground="#ef4444")  # red
        
        # Camera connection status color
        if self.status_vars["Camera"].get() == "Connected":
            self.status_labels_widgets["Camera"].configure(foreground="#10b981")  # green
        else:
            self.status_labels_widgets["Camera"].configure(foreground="#ef4444")  # red
        
        self.root.after(1000, self.update_time)

    def update_indicators(self):
        """Pulse animation for indicators."""
        self.camera_indicator.configure(foreground="#10b981" if self.is_running else "#ef4444")
        self.socket_indicator.configure(foreground="#10b981" if self.speed_estimator.mqtt_status == "Connected" else "#ef4444")
        self.root.after(2000, self.update_indicators)

    def start_camera(self):
        """Start the camera feed with fallback indices."""
        if self.cap is None:
            for index in [0]:  # Try index 0 only
                self.cap = cv2.VideoCapture(index)
                if self.cap.isOpened():
                    break
            if not self.cap.isOpened():
                self.status_vars["Camera"].set("Disconnected")
                self.cap = None
                return
            self.status_vars["Camera"].set("Connected")
        self.is_running = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.camera_feed.configure(text="")
        self.update_frame()

    def stop_camera(self):
        """Stop the camera feed."""
        self.is_running = False
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self.status_vars["Camera"].set("Disconnected")
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.camera_feed.configure(image='', text="Camera Feed Offline", fg="gray")
        self.speed_estimator.cleanup()

    def update_frame(self):
        """Update the camera feed frame."""
        if self.is_running and self.cap is not None:
            ret, frame = self.cap.read()
            if ret:
                frame = cv2.resize(frame, (1020, 500))
                processed_frame = self.speed_estimator.estimate_speed(frame)
                img = cv2.cvtColor(processed_frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(img)
                imgtk = ImageTk.PhotoImage(image=img)
                self.camera_feed.imgtk = imgtk
                self.camera_feed.configure(image=imgtk)
                self.status_vars["Total Detections"].set(str(self.speed_estimator.detection_counter))
                
                # Update vehicle log
                if self.speed_estimator.new_detections:
                    self.vehicle_log_text.configure(state="normal")
                    for detection in self.speed_estimator.new_detections:
                        log_text = f"#{detection.get('detection_count', 'N/A')} {detection['time']} - ID: {detection['track_id']}, Type: {detection['vehicle_type']}, Plate: {detection['numberplate']}, Speed: {detection['speed']} km/h, Gate: {detection.get('gate_status', 'N/A')}\n"
                        self.vehicle_log_text.insert(tk.END, log_text)
                    self.vehicle_log_text.configure(state="disabled")
                    self.vehicle_log_text.see(tk.END)  # Auto-scroll to bottom
                    self.speed_estimator.new_detections.clear()
                    
            self.root.after(10, self.update_frame)

    def run(self):
        """Run the GUI main loop."""
        try:
            self.root.mainloop()
        finally:
            self.speed_estimator.cleanup()

if __name__ == "__main__":
    gui = ParkingSystemGUI()
    gui.run()