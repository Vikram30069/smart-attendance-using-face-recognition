import os
import json
import numpy as np
import cv2
import face_recognition

def evaluate_model():
    dataset_dir = "Pics_dataset"
    if not os.path.exists(dataset_dir):
        print(f"Error: Dataset directory '{dataset_dir}' not found.")
        return

    print("Loading and encoding faces from dataset...")
    # Dictionary to hold encodings: { user_id: [enc1, enc2, ...] }
    user_encodings = {}
    
    # Traverse Pics_dataset
    subdirs = sorted([d for d in os.listdir(dataset_dir) if os.path.isdir(os.path.join(dataset_dir, d))])
    
    total_images_processed = 0
    for subdir in subdirs:
        user_id = subdir
        user_dir = os.path.join(dataset_dir, subdir)
        image_files = [f for f in os.listdir(user_dir) if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
        
        if not image_files:
            continue
            
        encodings = []
        for img_file in image_files:
            img_path = os.path.join(user_dir, img_file)
            try:
                # Load image using cv2/Pillow
                img = face_recognition.load_image_file(img_path)
                
                # Resize large images to match live app behavior and speed up evaluation
                h, w = img.shape[:2]
                max_dim = max(h, w)
                if max_dim > 800:
                    scale = 800.0 / float(max_dim)
                    new_w = int(w * scale)
                    new_h = int(h * scale)
                    img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
                    
                # Find face locations
                face_locations = face_recognition.face_locations(img, model="hog")
                if face_locations:
                    # Get encoding
                    encoding = face_recognition.face_encodings(img, face_locations)[0]
                    encodings.append(encoding)
                    total_images_processed += 1
            except Exception as e:
                print(f"Warning: Could not process {img_path}: {e}")
                
        if encodings:
            user_encodings[user_id] = encodings
            print(f"  User {user_id}: Loaded {len(encodings)} face encodings.")

    if len(user_encodings) < 2:
        print("Error: Need at least 2 users with face encodings to perform evaluation.")
        return

    print(f"\nTotal users loaded: {len(user_encodings)}")
    print(f"Total face encodings: {total_images_processed}")
    print("\nEvaluating model performance across different thresholds...")

    # We will test thresholds from 0.35 to 0.65 in steps of 0.05
    thresholds = [0.40, 0.45, 0.48, 0.50, 0.55, 0.60]
    
    # For evaluation, we simulate:
    # - The first encoding of each user as their "registered/db" profile face.
    # - The remaining encodings of each user as "test" faces.
    db_profiles = {}
    test_faces = {}
    
    for user_id, encs in user_encodings.items():
        db_profiles[user_id] = encs[0]
        if len(encs) > 1:
            test_faces[user_id] = encs[1:]

    if not test_faces:
        print("Error: No test images available. Each user folder must have at least 2 images.")
        return

    print(f"Registered profiles: {len(db_profiles)}")
    print(f"Test cases: {sum(len(v) for v in test_faces.values())}")

    print("\n" + "="*80)
    print(f"{'Threshold':<12} | {'Accuracy':<10} | {'Precision':<10} | {'Recall':<10} | {'FPR (False Acc.)':<16} | {'F1-Score':<10}")
    print("="*80)

    for threshold in thresholds:
        tp = 0  # True Positives: correct student matches within threshold
        fp = 0  # False Positives: incorrect student or imposter matches within threshold
        tn = 0  # True Negatives: incorrect student or imposter correctly rejected
        fn = 0  # False Negatives: correct student rejected

        # Run Genuine Matches (Testing if correct user is recognized)
        for user_id, encs in test_faces.items():
            db_enc = db_profiles[user_id]
            for test_enc in encs:
                dist = face_recognition.face_distance([db_enc], test_enc)[0]
                if dist <= threshold:
                    tp += 1
                else:
                    fn += 1

        # Run Imposter Matches (Testing if user B is rejected when trying to match user A)
        # For each test face of user A, we try matching it against every other user's DB profile.
        for user_id, encs in test_faces.items():
            for test_enc in encs:
                for other_user_id, db_enc in db_profiles.items():
                    if other_user_id == user_id:
                        continue
                    dist = face_recognition.face_distance([db_enc], test_enc)[0]
                    if dist <= threshold:
                        fp += 1  # Accepted someone else
                    else:
                        tn += 1  # Correctly rejected someone else

        # Calculate metrics
        accuracy = (tp + tn) / (tp + fp + tn + fn) if (tp + fp + tn + fn) > 0 else 0
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        fpr = fp / (fp + tn) if (fp + tn) > 0 else 0
        f1_score = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0

        print(f"{threshold:<12.2f} | {accuracy*100:<9.1f}% | {precision*100:<9.1f}% | {recall*100:<9.1f}% | {fpr*100:<15.1f}% | {f1_score:<10.3f}")

    print("="*80)
    print("\nMetric Definitions:")
    print("  - Accuracy: Overall correctness rate of matching & rejection decisions.")
    print("  - Precision: Out of all accepted matches, how many were actually correct.")
    print("  - Recall: Out of all genuine users, how many were correctly recognized.")
    print("  - FPR (False Acceptance Rate): Rate at which imposters are incorrectly marked present.")
    print("  - F1-Score: Harmonic mean of Precision and Recall (closer to 1.0 is better).")

if __name__ == '__main__':
    evaluate_model()
