import sqlite3
import json
import numpy as np

conn = sqlite3.connect('attendance.db')
c = conn.cursor()

# Check if test student already exists
c.execute("SELECT id FROM users WHERE username = 'teststudent'")
row = c.fetchone()
if row:
    c.execute("DELETE FROM users WHERE id = ?", (row[0],))

# Create dummy face encoding (array of 128 floats)
dummy_encoding = np.zeros(128).tolist()
face_encoding_str = json.dumps(dummy_encoding)

# Insert student
c.execute("""
    INSERT INTO users (username, email, password_hash, role, name, roll_number, department, section_id, year, face_encoding, created_at)
    VALUES ('teststudent', 'test@student.com', '240be518fabd2724ddb6f04eeb1da5967448d7e831c08c8fa822809f74c720a9', 
            'student', 'Test Student', 'CSE-101', 'Computer Science', 1, '2nd Year', ?, datetime('now'))
""", (face_encoding_str,))

conn.commit()
conn.close()
print("Success")
