import sqlite3
import datetime

conn = sqlite3.connect('attendance.db')
c = conn.cursor()

print("Updating sections table for CSE and IT...")
sections_data = [
    (1, 'CSE-A', 'Computer Science', '2nd Year'),
    (2, 'CSE-B', 'Computer Science', '2nd Year'),
    (3, 'CSE-C', 'Computer Science', '3rd Year'),
    (4, 'IT-A', 'Information Technology', '2nd Year'),
    (5, 'IT-B', 'Information Technology', '2nd Year'),
    (6, 'IT-C', 'Information Technology', '3rd Year')
]

for s_id, s_name, dept, yr in sections_data:
    c.execute("""
        UPDATE sections 
        SET section_name = ?, department = ?, year = ?
        WHERE id = ?
    """, (s_name, dept, yr, s_id))

# Sync user department and year if they belong to these sections
c.execute("UPDATE users SET department = 'Computer Science', year = '2nd Year' WHERE section_id IN (1, 2)")
c.execute("UPDATE users SET department = 'Computer Science', year = '3rd Year' WHERE section_id = 3")
c.execute("UPDATE users SET department = 'Information Technology', year = '2nd Year' WHERE section_id IN (4, 5)")
c.execute("UPDATE users SET department = 'Information Technology', year = '3rd Year' WHERE section_id = 6")

print("Ensuring Library/Sports Staff user exists...")
c.execute("SELECT id FROM users WHERE username = 'staff'")
staff_row = c.fetchone()
if staff_row:
    staff_id = staff_row[0]
else:
    now_str = datetime.datetime.utcnow().isoformat()
    c.execute("""
        INSERT INTO users (username, password_hash, role, name, created_at)
        VALUES ('staff', '240be518fabd2724ddb6f04eeb1da5967448d7e831c08c8fa822809f74c720a9', 'teacher', 'Library/Sports Staff', ?)
    """, (now_str,))
    staff_id = c.lastrowid

print("Clearing old schedules...")
c.execute("DELETE FROM class_schedule")

teachers = [
    {"id": 2, "name": "Dr. M Praveen Kumar", "subject": "Data Structures"},
    {"id": 3, "name": "Dr. T Raghunadha Reddy", "subject": "Python Programming"},
    {"id": 4, "name": "Dr. M Swapna", "subject": "Database Management"},
    {"id": 5, "name": "Ms. K Mrunalini", "subject": "Theory of Computation"},
    {"id": 6, "name": "Mr. Rajesh Kumar", "subject": "Microprocessors"},
    {"id": 7, "name": "Ms. Divya Sharma", "subject": "Embedded Systems"}
]

sections = [
    {"id": 1, "name": "CSE-A", "room": "N414"},
    {"id": 2, "name": "CSE-B", "room": "N415"},
    {"id": 3, "name": "CSE-C", "room": "N416"},
    {"id": 4, "name": "IT-A", "room": "IT301"},
    {"id": 5, "name": "IT-B", "room": "IT302"},
    {"id": 6, "name": "IT-C", "room": "IT303"}
]

days = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT']
periods = [
    ("09:30", "10:30"),
    ("10:30", "11:30"),
    ("11:30", "12:30"),
    ("13:10", "14:10"),
    ("14:10", "15:10"),
    ("15:10", "16:10") # Only for TUE, THU, SAT
]

now_str = datetime.datetime.utcnow().isoformat()

print("Generating college timetable...")
inserted_count = 0

for d_idx, day in enumerate(days):
    is_6_period = (day in ['TUE', 'THU', 'SAT'])
    daily_periods = periods if is_6_period else periods[:5]
    
    for p_idx, (start, end) in enumerate(daily_periods):
        # If it's Period 6 on a 6-period day, it is Library/Sports for all sections and Leisure for all teachers
        if p_idx == 5:
            # 1. Sections get Library or Sports
            subject = "Sports" if day == 'THU' else "Library"
            room = "Playground" if subject == "Sports" else "Central Library"
            for sec in sections:
                c.execute("""
                    INSERT INTO class_schedule 
                    (section_id, subject, day, start_time, end_time, room, teacher_id, latitude, longitude, created_at, class_name, teacher_name)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (sec["id"], subject, day, start, end, room, staff_id, 12.9715987, 77.594566, now_str, subject, "Library/Sports Staff"))
                inserted_count += 1
            
            # 2. Teachers get Leisure
            for t_idx, t in enumerate(teachers):
                c.execute("""
                    INSERT INTO class_schedule 
                    (section_id, subject, day, start_time, end_time, room, teacher_id, latitude, longitude, created_at, class_name, teacher_name)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (t_idx + 1, "Leisure", day, start, end, "Staff Room", t["id"], 12.9715987, 77.594566, now_str, "Leisure", t["name"]))
                inserted_count += 1
        else:
            # Academic periods (Periods 1 to 5)
            # We use Latin Square to assign teachers to sections: teacher_idx = (sec_idx + p_idx + d_idx) % 6
            for s_idx, sec in enumerate(sections):
                t_idx = (s_idx + p_idx + d_idx) % 6
                t = teachers[t_idx]
                c.execute("""
                    INSERT INTO class_schedule 
                    (section_id, subject, day, start_time, end_time, room, teacher_id, latitude, longitude, created_at, class_name, teacher_name)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (sec["id"], t["subject"], day, start, end, sec["room"], t["id"], 12.9715987, 77.594566, now_str, t["subject"], t["name"]))
                inserted_count += 1

conn.commit()
conn.close()
print(f"Successfully populated college weekly schedule! Inserted {inserted_count} slots.")
