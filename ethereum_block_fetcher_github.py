import json
import os
import time
import threading
from datetime import datetime
from web3 import Web3
from typing import List, Dict, Any, Optional, Tuple

class EthereumBlockFetcher:
    def __init__(self, rpc_url: str = "https://base.gateway.tenderly.co"):
        """
        Initialize the Ethereum block fetcher
        
        Args:
            rpc_url: Ethereum RPC endpoint URL
        """
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.eth_block_start = 35930447
        self.bwork_contract_address = "0x97bBa9F710DE42536843Bd1061514b5104D7DE50"
        self.mint_topic = "0xcf6fbb9dcea7d07263ab4f5c3a92f53af33dffc421d9d121e1c74b307e68189d"
        self.mined_blocks = []
        self.previous_challenge = None
        self.last_processed_block_file = "mined_blocks_mainnet.json"
        self.running = False
        self.scheduler_thread = None
                       
    def load_existing_data(self) -> Tuple[int, List, Optional[str]]:
        """Load existing data from file including mined blocks and previous challenge"""
        print(f"Checking file: {self.last_processed_block_file}")
        print(f"File exists: {os.path.exists(self.last_processed_block_file)}")
        
        # Also check in data directory for GitHub Actions
        data_file_path = f"data/{self.last_processed_block_file}"
        if os.path.exists(data_file_path):
            file_path = data_file_path
        elif os.path.exists(self.last_processed_block_file):
            file_path = self.last_processed_block_file
        else:
            print(f"No existing data file found")
            return self.eth_block_start, [], None
        
        try:
            with open(file_path, 'r') as f:
                content = f.read().strip()
                if not content:
                    print(f"File {file_path} is empty, using defaults")
                    return self.eth_block_start, [], None
                data = json.loads(content)
                print(f"Loaded data keys: {data.keys()}")
                
                # Load last processed block
                last_block = data.get('latest_block_number', self.eth_block_start)
                
                # Load existing mined blocks
                existing_blocks = data.get('mined_blocks', [])
                
                # Load previous challenge
                previous_challenge = data.get('previous_challenge', None)
                
                print(f"Loaded {len(existing_blocks)} existing mined blocks")
                print(f"Last processed block: {last_block}")
                print(f"Previous challenge: {previous_challenge}")
                
                return last_block, existing_blocks, previous_challenge
                
        except (json.JSONDecodeError, KeyError) as e:
            print(f"Error reading {file_path}: {e}, using defaults")
            return self.eth_block_start, [], None
    
    def load_last_processed_block(self) -> int:
        """Load the last processed block from file, or return default start block"""
        last_block, existing_blocks, previous_challenge = self.load_existing_data()
        
        # Update instance variables with loaded data
        self.mined_blocks = existing_blocks
        self.previous_challenge = previous_challenge
        
        return last_block

    def save_last_processed_block(self, block_number: int):
        """Save the last processed block to file"""
        data = {'last_block': block_number}
        with open(self.last_processed_block_file, 'w') as f:
            json.dump(data, f, indent=2)
    
    def get_miner_address_from_topic(self, topic: str) -> str:
        """Extract miner address from topic (assuming it's in the topic)"""
        # Remove '0x' prefix and take the last 40 characters (20 bytes = address)
        if topic.startswith('0x'):
            topic = topic[2:]
        # Ethereum addresses are 20 bytes, so last 40 hex characters
        if len(topic) >= 40:
            return '0x' + topic[-40:]
        return topic
    
    def fetch_logs(self, start_block: int, end_block: int) -> List[Dict[str, Any]]:
        """
        Fetch logs from Ethereum blockchain
        
        Args:
            start_block: Starting block number
            end_block: Ending block number
            
        Returns:
            List of log entries
        """
        try:
            logs = self.w3.eth.get_logs({
                'fromBlock': start_block,
                'toBlock': end_block,
                'address': self.bwork_contract_address,
                'topics': [self.mint_topic]
            })
            
            print(f"Got filter results: {len(logs)} transactions")
            return logs
            
        except Exception as e:
            print(f"Error fetching logs: {e}")
            return []

    def process_transaction(self, transaction: Dict[str, Any]):
        """Process a single transaction and update mined_blocks"""
        tx_hash = transaction['transactionHash'].hex()
        block_number = int(transaction['blockNumber'])
        
        # Get miner address from topics[1]
        miner_address = self.get_miner_address_from_topic(transaction['topics'][1].hex())
        
        # Process transaction data - need to handle bytes properly
        data = transaction['data']
        
        # Convert bytes to hex string if needed
        if isinstance(data, bytes):
            data = data.hex()
        elif isinstance(data, str) and data.startswith('0x'):
            data = data[2:]  # Remove 0x prefix
        elif isinstance(data, str):
            # Already a hex string without 0x
            pass
        else:
            print(f"Unexpected data type: {type(data)}")
            return
        
        # Extract amount (first 64 hex characters)
        if len(data) >= 64:
            data_amt_hex = data[:64]  # First 64 chars
            try:
                data_amt = int(data_amt_hex, 16) / (10 ** 18)  # Convert to ETH
            except ValueError as e:
                print(f"Error parsing amount: {e}")
                data_amt = 0
        else:
            data_amt = 0
        
        # Extract epochCount (next 64 chars after amount)
        if len(data) >= 128:
            epoch_count_hex = data[64:128]  # Characters 64-128
            try:
                epoch_count = int(epoch_count_hex, 16)
            except ValueError as e:
                print(f"Error parsing epoch count: {e}")
                epoch_count = 0
        else:
            epoch_count = 0

        # Extract challenger (next 64 chars)
        if len(data) >= 192:
            challenger = data[128:192]  # Characters 128-192
            
            if self.previous_challenge != challenger:
                previous_challenge2 = self.previous_challenge
                print(f"Old challenge: {self.previous_challenge}, new challenge: {challenger}")
                self.previous_challenge = challenger
                
                if previous_challenge2 is not None:
                    # Create new block entry for challenge change
                    first_block_num = self.mined_blocks[0][0] if self.mined_blocks else block_number
                    first_epoch_count = self.mined_blocks[0][4] if self.mined_blocks else 0
                    first_miner_address = self.mined_blocks[0][2] if self.mined_blocks else miner_address
                    first_tx_hash = self.mined_blocks[0][1] if self.mined_blocks else tx_hash
                    new_block = [first_block_num, first_tx_hash, first_miner_address, -1, first_epoch_count]
                    self.mined_blocks.insert(0, new_block)
        
        # Add the actual mined block
        self.mined_blocks.insert(0, [block_number, tx_hash, miner_address, data_amt, epoch_count])
        print(f"Processed block {block_number}: {miner_address}, amount: {data_amt}, epoch: {epoch_count}")
        
    def save_mined_blocks_to_file(self, current_block, filename: str = "mined_blocks_mainnet.json"):
        """Save mined blocks to JSON files - simplified for GitHub Actions"""
        output_data = {
            'mined_blocks': self.mined_blocks,
            'total_blocks': len(self.mined_blocks),
            'last_updated': int(time.time()),
            'latest_block_number': current_block,
            'contract_address': self.bwork_contract_address,
            'mint_topic': self.mint_topic,
            'previous_challenge': self.previous_challenge
        }
        
        try:
            with open(filename, 'w') as f:
                json.dump(output_data, f, indent=2)
            print(f"Saved {len(self.mined_blocks)} mined blocks to {filename}")
        except Exception as e:
            print(f"Error saving file {filename}: {e}")
    
    def run_github_actions(self, batch_size: int = 499, max_runtime_minutes: int = 20):
        """
        Run the fetcher optimized for GitHub Actions with time limits
        
        Args:
            batch_size: Number of blocks to process in each batch
            max_runtime_minutes: Maximum runtime in minutes to avoid timeout
        """
        start_time = time.time()
        max_runtime_seconds = max_runtime_minutes * 60
        
        try:
            print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting GitHub Actions block fetch...")
            
            # Load the last processed block
            start_block = self.load_last_processed_block()
            current_block = self.w3.eth.get_block('latest')['number']
            
            print(f"Starting from block: {start_block}")
            print(f"Current block: {current_block}")
            
            if start_block > current_block:
                print("Already up to date!")
                return
            
            # Process blocks in batches with time checking
            current_start = start_block
            loop_counter = 0
            
            while current_start <= current_block:
                # Check if we're approaching time limit
                elapsed_time = time.time() - start_time
                if elapsed_time > max_runtime_seconds:
                    print(f"Approaching time limit ({max_runtime_minutes} min), stopping gracefully")
                    break
                
                time.sleep(0.5)  # Reduced sleep for faster processing
                current_end = min(current_start + batch_size - 1, current_block)
                
                print(f"Processing blocks {current_start} to {current_end} (Loop {loop_counter + 1})")
                
                # Fetch logs for this batch
                logs = self.fetch_logs(current_start, current_end)
                
                # Process each transaction
                for transaction in logs:
                    self.process_transaction(transaction)
                
                # Save progress every 10 loops or when approaching time limit
                loop_counter += 1
                if loop_counter % 10 == 0 or elapsed_time > (max_runtime_seconds * 0.8):
                    print(f"Saving progress after {loop_counter} loops...")
                    self.save_mined_blocks_to_file(current_end)
                
                # Move to next batch
                current_start = current_end + 1
            
            # Save final results
            print("Saving final results...")
            self.save_mined_blocks_to_file(current_end if 'current_end' in locals() else start_block)
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Processing complete!")
            
        except Exception as e:
            print(f"Error during fetch: {e}")
            # Save whatever progress we made
            if hasattr(self, 'mined_blocks') and len(self.mined_blocks) > 0:
                self.save_mined_blocks_to_file(start_block)

# GitHub Actions optimized main execution
if __name__ == "__main__":
    try:
        # Get RPC URL from environment variable with better fallback handling
        RPC_URL = os.environ.get('RPC_URL')
        if not RPC_URL:
            # Use the default from your original script if no environment variable
            RPC_URL = "https://base.gateway.tenderly.co"
            print(f"No RPC_URL environment variable found, using default RPC endpoint")
        else:
            print(f"Using RPC_URL from environment variable")
        
        print(f"Connecting to RPC: {RPC_URL[:50]}...")  # Show first 50 chars for debugging
        
        fetcher = EthereumBlockFetcher(RPC_URL)
        
        # Test connection first
        try:
            latest_block = fetcher.w3.eth.get_block('latest')
            print(f"Successfully connected to RPC. Latest block: {latest_block['number']}")
        except Exception as e:
            print(f"Failed to connect to RPC: {e}")
            exit(1)
        
        # Run once for GitHub Actions (no infinite loop)
        fetcher.run_github_actions(batch_size=499, max_runtime_minutes=20)
        
    except Exception as e:
        print(f"Error during main execution: {e}")
        exit(1)  # Exit with error code for GitHub Actions
