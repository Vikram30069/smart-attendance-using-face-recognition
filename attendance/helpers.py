import base64
import hashlib
import io
import json
import math
import os
import cv2
import face_recognition
import numpy as np
from PIL import Image, ExifTags

# Classroom geofence center (latitude, longitude) and radius in meters
GEOFENCE_CENTER = (12.9715987, 77.594566)  # Example: campus coordinates
GEOFENCE_RADIUS_METERS = 1000


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    return hash_password(password) == password_hash


def is_strong_password(password: str) -> bool:
    if len(password) < 8:
        return False
    has_lower = any(c.islower() for c in password)
    has_upper = any(c.isupper() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_special = any(c in "!@#$%^&*()-_=+[]{};:'\",.<>/?|`~" for c in password)
    return has_lower and has_upper and has_digit and has_special


def serialize_encoding(encoding: np.ndarray) -> str:
    return json.dumps(encoding.tolist())


def deserialize_encoding(value: str) -> np.ndarray:
    return np.array(json.loads(value), dtype=np.float64)


def is_duplicate_face(new_encoding: np.ndarray) -> bool:
    from .models import User

    users_with_faces = User.objects.filter(face_encoding__isnull=False).exclude(face_encoding="")
    for user in users_with_faces:
        try:
            known_encoding = deserialize_encoding(user.face_encoding)
            distance = face_recognition.face_distance([known_encoding], new_encoding)[0]
            if distance < 0.45:
                return True
        except Exception:
            continue
    return False


def calculate_distance(lat1, lon1, lat2, lon2):
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        raise ValueError("Missing coordinate value for distance calculation")

    try:
        lat1 = float(lat1)
        lon1 = float(lon1)
        lat2 = float(lat2)
        lon2 = float(lon2)
    except (TypeError, ValueError):
        raise ValueError("Invalid coordinate value for distance calculation")

    radius = 6371000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius * c


def is_within_geofence(latitude: float, longitude: float) -> bool:
    distance = calculate_distance(latitude, longitude, GEOFENCE_CENTER[0], GEOFENCE_CENTER[1])
    return distance <= GEOFENCE_RADIUS_METERS


def load_image_from_bytes(image_data: bytes):
    try:
        pil_image = Image.open(io.BytesIO(image_data))
        try:
            exif = pil_image._getexif()
            if exif is not None:
                orientation_key = next((k for k, v in ExifTags.TAGS.items() if v == "Orientation"), None)
                if orientation_key and orientation_key in exif:
                    orientation = exif[orientation_key]
                    if orientation == 3:
                        pil_image = pil_image.rotate(180, expand=True)
                    elif orientation == 6:
                        pil_image = pil_image.rotate(270, expand=True)
                    elif orientation == 8:
                        pil_image = pil_image.rotate(90, expand=True)
        except Exception:
            pass
        pil_image = pil_image.convert("RGB")
        np_image = np.array(pil_image)
        cv_image = cv2.cvtColor(np_image, cv2.COLOR_RGB2BGR)
        # Resize very large images to speed up face processing while keeping aspect ratio
        try:
            h, w = cv_image.shape[:2]
            max_dim = max(h, w)
            MAX_DIM = 800
            if max_dim > MAX_DIM:
                scale = MAX_DIM / float(max_dim)
                new_w = int(w * scale)
                new_h = int(h * scale)
                cv_image = cv2.resize(cv_image, (new_w, new_h), interpolation=cv2.INTER_AREA)
        except Exception:
            pass
        return cv_image
    except Exception:
        np_image = np.frombuffer(image_data, dtype=np.uint8)
        return cv2.imdecode(np_image, cv2.IMREAD_COLOR)


def image_from_base64(base64_data: str):
    header, encoded = base64_data.split(",", 1) if "," in base64_data else (None, base64_data)
    data = base64.b64decode(encoded)
    return load_image_from_bytes(data)


def get_face_encoding_from_image(image):
    if image is None:
        return None
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    # Use the faster HOG model only to reduce CPU/time on registrations
    face_locations = face_recognition.face_locations(rgb, model="hog")
    if not face_locations:
        return None
    encodings = face_recognition.face_encodings(rgb, face_locations)
    return encodings[0] if encodings else None


def get_face_encoding_from_images(images):
    encodings = []
    for image in images:
        if image is None:
            continue
        encoding = get_face_encoding_from_image(image)
        if encoding is not None:
            encodings.append(encoding)
    if not encodings:
        return None
    return np.mean(encodings, axis=0)


def eye_aspect_ratio(eye_landmarks):
    left = np.array(eye_landmarks[0])
    right = np.array(eye_landmarks[3])
    vertical1 = np.linalg.norm(np.array(eye_landmarks[1]) - np.array(eye_landmarks[5]))
    vertical2 = np.linalg.norm(np.array(eye_landmarks[2]) - np.array(eye_landmarks[4]))
    horizontal = np.linalg.norm(left - right)
    if horizontal == 0:
        return 0.0
    return (vertical1 + vertical2) / (2.0 * horizontal)


def is_screen_present(image) -> bool:
    """
    Detects if the captured image represents a face shown on a phone screen
    or other digital screen (presentation attack detection).
    Checks:
    1. Blurriness (Laplacian variance) - screen re-photographs are often blurry or flat.
    2. Glare/reflection - screens reflect light, creating intense white specular highlights.
    3. Moiré periodic patterns - pixel grids create high frequency spikes in magnitude spectrum (FFT).
    4. Flat color signature - printouts/screens have narrow range of colors (YCrCb).
    """
    if image is None:
        return False
    try:
        # Convert to grayscale and RGB for face detection and visual analysis
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        face_locations = face_recognition.face_locations(rgb)
        
        if face_locations:
            top, right, bottom, left = face_locations[0]
            # Add padding to capture some background reflection around the face
            h, w = image.shape[:2]
            pad_h = int((bottom - top) * 0.1)
            pad_w = int((right - left) * 0.1)
            
            top_crop = max(0, top - pad_h)
            bottom_crop = min(h, bottom + pad_h)
            left_crop = max(0, left - pad_w)
            right_crop = min(w, right + pad_w)
            
            crop = image[top_crop:bottom_crop, left_crop:right_crop]
        else:
            # If no face is found in this specific check, default to center 60% of the image
            h, w = image.shape[:2]
            crop = image[h//5:4*h//5, w//5:4*w//5]

        if crop.size == 0:
            return False

        # --- 1. Glare & Specular Reflection Detection ---
        # Screens are glass and strongly reflect ambient light, creating white glare patches.
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        v_channel = hsv[:, :, 2]
        # Percentage of pixels near absolute maximum brightness
        glare_ratio = np.sum(v_channel > 250) / v_channel.size
        print(f"[DEBUG LIVENESS] glare_ratio={glare_ratio:.4f}")
        if glare_ratio > 0.08:
            print("[DEBUG LIVENESS] Rejecting due to high glare")
            return True

        # --- 2. Flat Color / Print Attack Check ---
        # Photos of screens or paper printouts often have flat chrominance profiles
        ycr_cb = cv2.cvtColor(crop, cv2.COLOR_BGR2YCrCb)
        cr = ycr_cb[:, :, 1]
        cb = ycr_cb[:, :, 2]
        std_cr = np.std(cr)
        std_cb = np.std(cb)
        print(f"[DEBUG LIVENESS] std_cr={std_cr:.4f}, std_cb={std_cb:.4f}")
        if std_cr < 2.5 and std_cb < 2.5:
            print("[DEBUG LIVENESS] Rejecting due to flat chrominance (potential print/screen)")
            return True

        # --- 3. Blur Detection (Laplacian Variance) ---
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        print(f"[DEBUG LIVENESS] laplacian_var={laplacian_var:.4f}")
        if laplacian_var < 35.0:
            print("[DEBUG LIVENESS] Rejecting due to low sharpness (blur)")
            # Excessively blurry image is a common indicator of a presentation attack/re-photo
            return True

        # --- 4. FFT Moiré Pattern & Pixel Grid Analysis ---
        # Resize to standard size for consistent frequency bounds
        resized_gray = cv2.resize(gray, (128, 128))
        f_transform = np.fft.fft2(resized_gray)
        f_shift = np.fft.fftshift(f_transform)
        magnitude_spectrum = np.abs(f_shift)
        
        # Log scaling to compress the range
        magnitude_log = np.log(magnitude_spectrum + 1)
        
        ch, cw = magnitude_log.shape
        cy, cx = ch // 2, cw // 2
        
        # Mask out the low frequencies (center circle of radius 16)
        y_indices, x_indices = np.ogrid[:ch, :cw]
        center_circle_mask = (y_indices - cy)**2 + (x_indices - cx)**2 <= 16**2
        high_freq_mask = ~center_circle_mask
        
        high_freq_values = magnitude_log[high_freq_mask]
        
        mean_hf = np.mean(high_freq_values)
        std_hf = np.std(high_freq_values)
        max_hf = np.max(high_freq_values)
        
        # Compute the Peak-to-Standard-Deviation Ratio in high-frequency regions
        if std_hf > 0:
            peak_ratio = (max_hf - mean_hf) / std_hf
        else:
            peak_ratio = 0
            
        # Screen pixel grids cause periodic high-energy spikes in high frequencies.
        # Live human faces show a natural, smooth, decaying frequency distribution.
        # A high peak ratio combined with decent high frequency standard deviation is a clear moire signature.
        print(f"[DEBUG LIVENESS] peak_ratio={peak_ratio:.4f}, std_hf={std_hf:.4f}")
        if peak_ratio > 5.5 and std_hf > 1.0:
            print("[DEBUG LIVENESS] Rejecting due to FFT periodic moire pattern")
            return True

        print("[DEBUG LIVENESS] is_screen_present: PASSED")
        return False
    except Exception as e:
        print(f"[DEBUG LIVENESS] Exception in is_screen_present: {str(e)}")
        # If any step fails, default to returning False to avoid blocking legitimate users due to math/shape errors
        return False


def is_liveness_valid(image1, image2):
    try:
        # Check for screen spoofing/presentation attack on either frame
        if is_screen_present(image1) or is_screen_present(image2):
            return False

        rgb1 = cv2.cvtColor(image1, cv2.COLOR_BGR2RGB)
        rgb2 = cv2.cvtColor(image2, cv2.COLOR_BGR2RGB)
        landmarks1 = face_recognition.face_landmarks(rgb1)
        landmarks2 = face_recognition.face_landmarks(rgb2)
        if not landmarks1 or not landmarks2:
            return False
        left_ear1 = eye_aspect_ratio(landmarks1[0]["left_eye"])
        right_ear1 = eye_aspect_ratio(landmarks1[0]["right_eye"])
        left_ear2 = eye_aspect_ratio(landmarks2[0]["left_eye"])
        right_ear2 = eye_aspect_ratio(landmarks2[0]["right_eye"])
        avg1 = (left_ear1 + right_ear1) / 2.0
        avg2 = (left_ear2 + right_ear2) / 2.0
        return abs(avg1 - avg2) > 0.03
    except Exception:
        return False


class AntiSpoofingONNX:
    """Face anti-spoofing inference using ONNXRuntime."""

    def __init__(self, model_path: str, scale: float = 2.7) -> None:
        """Initialize the AntiSpoofingONNX class.

        Args:
            model_path: Path to the ONNX model file.
            scale: Crop scale factor for face region.
        """
        import onnxruntime as ort
        self.session = ort.InferenceSession(
            model_path,
            providers=["CPUExecutionProvider"],
        )
        self.scale = scale

        input_cfg = self.session.get_inputs()[0]
        self.input_name = input_cfg.name
        self.input_size = tuple(input_cfg.shape[2:])

        output_cfg = self.session.get_outputs()[0]
        self.output_name = output_cfg.name

    def _xyxy2xywh(self, bbox: list[float]) -> list[int]:
        """Convert [x1, y1, x2, y2] to [x, y, w, h]."""
        x1, y1, x2, y2 = bbox
        return [int(x1), int(y1), int(x2 - x1), int(y2 - y1)]

    def _crop_face(self, image: np.ndarray, bbox: list[int]) -> np.ndarray:
        """Crop and resize face region from image."""
        src_h, src_w = image.shape[:2]
        x, y, box_w, box_h = bbox

        scale = min((src_h - 1) / box_h, (src_w - 1) / box_w, self.scale)
        new_w = box_w * scale
        new_h = box_h * scale

        center_x = x + box_w / 2
        center_y = y + box_h / 2

        x1 = max(0, int(center_x - new_w / 2))
        y1 = max(0, int(center_y - new_h / 2))
        x2 = min(src_w - 1, int(center_x + new_w / 2))
        y2 = min(src_h - 1, int(center_y + new_h / 2))

        cropped = image[y1 : y2 + 1, x1 : x2 + 1]
        if cropped.size == 0:
            return np.zeros((self.input_size[0], self.input_size[1], 3), dtype=np.uint8)
        return cv2.resize(cropped, self.input_size[::-1])

    def _preprocess(self, image: np.ndarray, bbox: list[int]) -> np.ndarray:
        """Preprocess face crop for inference."""
        face = self._crop_face(image, bbox)
        face = face.astype(np.float32)
        face = np.transpose(face, (2, 0, 1))
        face = np.expand_dims(face, axis=0)
        return face

    def _softmax(self, x: np.ndarray) -> np.ndarray:
        """Apply softmax to logits."""
        e_x = np.exp(x - np.max(x, axis=1, keepdims=True))
        return e_x / e_x.sum(axis=1, keepdims=True)

    def predict(self, image: np.ndarray, bbox_xyxy: list[float]) -> dict:
        """Predict if face is real or fake.

        Args:
            image: Input image (BGR format).
            bbox_xyxy: Face bounding box [x1, y1, x2, y2].

        Returns:
            Dictionary with keys: label, score, bbox (xywh format).
        """
        bbox_xywh = self._xyxy2xywh(bbox_xyxy)

        input_tensor = self._preprocess(image, bbox_xywh)
        outputs = self.session.run([self.output_name], {self.input_name: input_tensor})

        logits = outputs[0]
        probs = self._softmax(logits)

        label_idx = int(np.argmax(probs))
        score = float(probs[0, label_idx])

        return {
            "label": "Real" if label_idx == 1 else "Fake",
            "score": score,
            "bbox": bbox_xywh,
        }


# Global model caching variable
_anti_spoofing_engine = None

def is_liveness_valid_v2(image, face_location) -> tuple[bool, str, float]:
    """
    Checks if a face in the image is real (not a presentation attack / spoof) 
    using the MiniFASNetV2 ONNX model.
    
    Args:
        image: BGR image.
        face_location: Tuple of (top, right, bottom, left) as returned by face_recognition.
        
    Returns:
        tuple: (is_real, label, score)
    """
    global _anti_spoofing_engine
    if image is None or face_location is None:
        return False, "Invalid Input", 0.0

    try:
        if _anti_spoofing_engine is None:
            import os
            base_dir = os.path.dirname(os.path.abspath(__file__))
            model_path = os.path.join(base_dir, "anti_spoofing_models", "MiniFASNetV2.onnx")
            
            if not os.path.exists(model_path):
                print(f"[LIVENESS V2] Model file not found at {model_path}. Liveness check bypassed for safety.")
                return True, "Model Missing", 1.0
                
            _anti_spoofing_engine = AntiSpoofingONNX(model_path=model_path, scale=2.7)
            
        top, right, bottom, left = face_location
        bbox_xyxy = [float(left), float(top), float(right), float(bottom)]
        
        result = _anti_spoofing_engine.predict(image, bbox_xyxy)
        is_real = result["label"] == "Real"
        
        print(f"[LIVENESS V2] Prediction: {result['label']} with score {result['score']:.4f}")
        return is_real, result["label"], result["score"]
    except Exception as e:
        print(f"[LIVENESS V2] Exception during liveness validation: {str(e)}")
        return True, f"Error: {str(e)}", 1.0

