from utils.slurm_client import SlurmClient
import logging

# Setup basic logging to see warnings/errors
logging.basicConfig(level=logging.INFO)

def main():
    print("Connecting to Slurm Cluster...")
    client = SlurmClient()
    
    try:
        conn = client.get_connection()
        print("Connected. Running sinfo...")
        
        # Run sinfo without filtering
        result = conn.run('sinfo -o "%P %n %T"', hide=True)
        if result.ok:
            print("\n--- RAW SINFO OUTPUT ---")
            print(result.stdout)
            print("------------------------\n")
            
            lines = result.stdout.strip().split('\n')
            if lines: lines = lines[1:] # Skip header
            
            partitions = set()
            nodes_by_partition = {}
            
            for line in lines:
                parts = line.split()
                if len(parts) >= 2:
                    p = parts[0].replace("*", "")
                    n = parts[1]
                    partitions.add(p)
                    if p not in nodes_by_partition:
                        nodes_by_partition[p] = []
                    nodes_by_partition[p].append(n)
            
            print(f"Found Partitions: {partitions}")
            for p, nodes in nodes_by_partition.items():
                print(f"Partition '{p}': {nodes}")
                
        else:
            print("sinfo command failed.")
            
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if 'conn' in locals() and conn:
            conn.close()

if __name__ == "__main__":
    main()
