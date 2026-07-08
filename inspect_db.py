import sqlite3

def inspect():
    conn = sqlite3.connect('attendance.db')
    cursor = conn.cursor()
    
    # Fetch user tables (ignoring internal Django/auth system tables)
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    all_tables = [row[0] for row in cursor.fetchall()]
    user_tables = [
        t for t in all_tables 
        if not t.startswith('django_') 
        and not t.startswith('auth_') 
        and not t.startswith('sqlite_')
    ]
    
    print("\n" + "="*40)
    print("   SQLITE DATABASE INSPECTOR (attendance.db)   ")
    print("="*40)
    print("Found the following tables and record counts:")
    
    for table in user_tables:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        print(f"  • {table:<22} : {count} records")
        
    print("\nChoose a table to preview (or press Enter to exit):")
    for idx, table in enumerate(user_tables, 1):
        print(f"  [{idx}] {table}")
        
    choice = input("\nEnter number (e.g. 1): ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(user_tables):
        selected_table = user_tables[int(choice) - 1]
        
        # Get column names
        cursor.execute(f"PRAGMA table_info({selected_table});")
        columns = [col[1] for col in cursor.fetchall()]
        
        # Get rows
        cursor.execute(f"SELECT * FROM {selected_table} LIMIT 20;")
        rows = cursor.fetchall()
        
        print("\n" + "="*60)
        print(f" PREVIEW: {selected_table.upper()} (Showing up to 20 rows)")
        print("="*60)
        
        # Print column headers
        col_line = " | ".join(columns)
        print(col_line)
        print("-" * len(col_line))
        
        for row in rows:
            print(" | ".join(str(val) for val in row))
        print("="*60 + "\n")
    
    conn.close()

if __name__ == '__main__':
    try:
        inspect()
    except Exception as e:
        print(f"Error reading database: {e}")
