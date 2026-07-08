import sqlite3

def get_attendance_with_names():
    # Connect to the SQLite database
    conn = sqlite3.connect('attendance.db')
    cursor = conn.cursor()
    
    # SQL Query to join attendance with users to get names and roll numbers
    query = """
        SELECT 
            u.name AS student_name,
            u.roll_number,
            u.role,
            a.date,
            a.time,
            a.status,
            a.location
        FROM 
            attendance a
        JOIN 
            users u ON a.user_id = u.id
        ORDER BY 
            a.date DESC, 
            a.time DESC;
    """
    
    try:
        cursor.execute(query)
        rows = cursor.fetchall()
        
        if not rows:
            print("\nNo attendance records found.")
            return
            
        print("\n" + "="*85)
        print(f" {'STUDENT NAME':<25} | {'ROLL NO':<12} | {'DATE':<10} | {'TIME':<8} | {'STATUS':<8}")
        print("="*85)
        
        for row in rows:
            name, roll, role, date, time, status, location = row
            # Format the output row
            print(f" {name or 'N/A':<25} | {roll or 'N/A':<12} | {date:<10} | {time:<8} | {status:<8}")
            
        print("="*85 + "\n")
        
    except Exception as e:
        print(f"Error fetching data: {e}")
    finally:
        conn.close()

if __name__ == '__main__':
    get_attendance_with_names()
