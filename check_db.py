import sqlite3
import os

def check_database(db_path, table_name):
    """Check contents of a SQLite database table"""
    if not os.path.exists(db_path):
        print(f"Database file not found: {db_path}")
        return
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Get table schema
        cursor.execute(f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table_name}'")
        schema = cursor.fetchone()
        if schema:
            print(f"\nSchema for {table_name}:")
            print(schema[0])
        
        # Get row count
        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        count = cursor.fetchone()[0]
        print(f"\nNumber of rows in {table_name}: {count}")
        
        # Get all rows
        cursor.execute(f"SELECT * FROM {table_name}")
        rows = cursor.fetchall()
        
        if rows:
            print(f"\nContents of {table_name}:")
            # Get column names
            columns = [description[0] for description in cursor.description]
            print("Columns:", columns)
            
            # Print each row
            for row in rows:
                print("\nRow:", row)
        else:
            print(f"\nNo data found in {table_name}")
            
    except sqlite3.Error as e:
        print(f"Database error: {e}")
    finally:
        if conn:
            conn.close()

def main():
    # Check memory database
    print("\n=== Checking Memory Database ===")
    check_database("tmp/memory.db", "memory")
    
    # Check agent sessions database
    print("\n=== Checking Agent Sessions Database ===")
    check_database("tmp/persistent_memory.db", "agent_sessions")

if __name__ == "__main__":
    main() 