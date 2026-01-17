import json
import time
import requests
import os
import random
from dataclasses import dataclass, asdict
from typing import List, Dict, Set, Tuple, Optional
from web3 import Web3
from datetime import datetime


@dataclass
class PoolKey:
    currency0: str
    currency1: str
    fee: int
    tickSpacing: int
    hooks: str
    
    def matches_target(self, target: 'PoolKey') -> bool:
        return (self.currency0.lower() == target.currency0.lower() and
                self.currency1.lower() == target.currency1.lower() and
                self.fee == target.fee and
                self.tickSpacing == target.tickSpacing and
                self.hooks.lower() == target.hooks.lower())

@dataclass
class Position:
    token_id: int
    pool_key: PoolKey
    owner: str
    block_number: int
    tx_hash: str
    timestamp: str

class UniswapV4Monitor:
    def __init__(self, rpc_url: str, start_block: int, save_file: str = "uniswap_v4_data.json", save_file_server: str = "/var/www/html/data.bzerox.org/mainnet/mainnet_uniswap_v4_data.json"):
        self.rpc_url = rpc_url
        self.web3 = Web3(Web3.HTTPProvider(rpc_url))
        self.start_block = start_block
        self.current_block = start_block
        self.save_file = save_file
        self.save_file_server = save_file_server
        self.max_logs_per_request = 499
        self.max_blocks_per_request = 499  # Limit block range for eth_getLogs
        
        # Retry configuration
        self.max_retries = 5
        self.base_retry_delay = 1.0
        self.max_retry_delay = 60.0
        
        # Contract addresses
        self.nft_address = "0x7C5f5A4bBd8fD63184577525326123B519429bDc"
        
        self.transfer_topic = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

        # Target pool key - B0x/0xBTC pool
        # Pool ID: 0x6d7608e5974f1aa1bc8ac9b33ae7fdd41a55b24f53007a7f5ed41ee5b15fb194
        self.target_pool_key = PoolKey(
            currency0="0x6B19E31C1813cD00b0d47d798601414b79A3e8AD",  # B0x
            currency1="0xc4D4FD4F4459730d176844c170F2bB323c87Eb3B",  # 0xBTC
            fee=8388608,
            tickSpacing=60,
            hooks="0x785319f8fCE23Cd733DE94Fd7f34b74A5cAa1000"
        )
        
        # Storage
        self.valid_positions: List[Position] = []
        self.invalid_positions: List[Position] = []
        self.nft_owners: Dict[int, str] = {}  # token_id -> owner
        
        # Load existing data if available
        self.load_data()
        
        # Clean up any invalid data from previous runs
        self.cleanup_invalid_data()
        
        print(f"Initialized Uniswap V4 Monitor")
        print(f"Starting from block: {self.current_block}")
        print(f"NFT Contract: {self.nft_address}")
        print(f"Save file: {self.save_file}")
        print(f"Max logs per request: {self.max_logs_per_request}")
        print(f"Max blocks per request: {self.max_blocks_per_request}")

    def cleanup_invalid_data(self):
        """Remove any tracked NFT owners that don't have valid positions"""
        if not self.nft_owners:
            return
            
        valid_token_ids = {pos.token_id for pos in self.valid_positions}
        original_count = len(self.nft_owners)
        
        # Remove NFT owners that don't have corresponding valid positions
        self.nft_owners = {token_id: owner for token_id, owner in self.nft_owners.items() 
                          if token_id in valid_token_ids}
        
        removed_count = original_count - len(self.nft_owners)
        if removed_count > 0:
            print(f"Cleaned up {removed_count} invalid NFT ownership records")
            # Save the cleaned data
            self.save_data()

    def exponential_backoff_delay(self, attempt: int) -> float:
        """Calculate exponential backoff delay with jitter"""
        delay = min(self.base_retry_delay * (2 ** attempt), self.max_retry_delay)
        # Add jitter to prevent thundering herd
        jitter = delay * 0.1 * random.random()
        return delay + jitter

    def retry_with_backoff(self, func, *args, **kwargs):
        """Execute function with exponential backoff retry logic"""
        last_exception = None
        
        for attempt in range(self.max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                if attempt == self.max_retries - 1:
                    # Last attempt failed
                    break
                
                delay = self.exponential_backoff_delay(attempt)
                print(f"Attempt {attempt + 1} failed: {str(e)[:100]}...")
                print(f"Retrying in {delay:.2f} seconds...")
                time.sleep(delay)
        
        # All attempts failed
        print(f"All {self.max_retries} attempts failed. Last error: {last_exception}")
        raise last_exception

    def rate_limit_pause(self):
        """Pause between RPC calls"""
        time.sleep(1.25)

    def save_data(self):
        """Save current state to JSON file"""

        print(f"DEBUG: self.nft_owners has {len(self.nft_owners)} entries")
        print(f"DEBUG: self.valid_positions has {len(self.valid_positions)} entries")
    
        valid_token_ids = {pos.token_id for pos in self.valid_positions}
        print(f"DEBUG: valid_token_ids: {valid_token_ids}")
    
        # Filter nft_owners to only include those with valid positions
        valid_nft_owners = {token_id: owner for token_id, owner in self.nft_owners.items() 
                           if token_id in valid_token_ids}
        print(f"DEBUG: valid_nft_owners has {len(valid_nft_owners)} entries")
    

        valid_token_ids = {pos.token_id for pos in self.valid_positions}
            
        # Filter nft_owners to only include those with valid positions
        valid_nft_owners = {token_id: owner for token_id, owner in self.nft_owners.items() 
            if token_id in valid_token_ids}
        try:
            data = {
                "metadata": {
                    "last_updated": datetime.now().isoformat(),
                    "current_block": self.current_block,
                    "start_block": self.start_block,
                    "target_pool_key": asdict(self.target_pool_key),
                    "total_valid_positions": len(self.valid_positions),
                    "total_invalid_positions": len(self.invalid_positions),
                    "total_nft_owners": len(valid_nft_owners)
                },
                "valid_positions": [
                    {
                        "token_id": pos.token_id,
                        "pool_key": asdict(pos.pool_key),
                        "owner": pos.owner,
                        "block_number": pos.block_number,
                        "tx_hash": pos.tx_hash,
                        "timestamp": pos.timestamp
                    } for pos in self.valid_positions
                ],
                "nft_owners": valid_nft_owners
            }
            
            # Write to temporary file first, then rename (atomic operation)
            temp_file = self.save_file + ".tmp"
            with open(temp_file, 'w') as f:
                json.dump(data, f, indent=2)
            
            os.rename(temp_file, self.save_file)
            print(f"Data saved to {self.save_file}")
            
            # Only save to server file if the path exists (skip for GitHub Actions)
            try:
                if not os.environ.get('GITHUB_ACTIONS'):  # Skip server save in GitHub Actions
                    temp_file_server = self.save_file_server + ".tmp"
                    with open(temp_file_server, 'w') as f:
                        json.dump(data, f, indent=2)
                    
                    os.rename(temp_file_server, self.save_file_server)
                    print(f"Data saved to {self.save_file_server}")
            except Exception as server_e:
                print(f"Could not save to server file: {server_e}")
            
        except Exception as e:
            print(f"Error saving data: {e}")

    def load_data(self):
        """Load existing data from JSON file"""
        # Check multiple possible locations for GitHub Actions
        possible_paths = [
            f"mainnetB0x/{self.save_file}",  # GitHub repo structure
            self.save_file                   # Current directory
        ]
        
        file_path = None
        for path in possible_paths:
            if os.path.exists(path):
                file_path = path
                print(f"Found existing file at: {path}")
                break
        
        if not file_path:
            print(f"No existing save file found")
            return
            
        try:
            with open(file_path, 'r') as f:
                content = f.read().strip()
                if not content:
                    print(f"File {file_path} is empty, starting fresh")
                    return
                data = json.loads(content)
            
            # Load metadata
            metadata = data.get("metadata", {})
            self.current_block = metadata.get("current_block", self.start_block)
            
            # Load valid positions
            for pos_data in data.get("valid_positions", []):
                pool_key = PoolKey(**pos_data["pool_key"])
                position = Position(
                    token_id=pos_data["token_id"],
                    pool_key=pool_key,
                    owner=pos_data["owner"],
                    block_number=pos_data["block_number"],
                    tx_hash=pos_data["tx_hash"],
                    timestamp=pos_data["timestamp"]
                )
                self.valid_positions.append(position)
            
            # Load invalid positions
            for pos_data in data.get("invalid_positions", []):
                pool_key = PoolKey(**pos_data["pool_key"])
                position = Position(
                    token_id=pos_data["token_id"],
                    pool_key=pool_key,
                    owner=pos_data["owner"],
                    block_number=pos_data["block_number"],
                    tx_hash=pos_data["tx_hash"],
                    timestamp=pos_data["timestamp"]
                )
                self.invalid_positions.append(position)
            
            # Load NFT owners
            self.nft_owners = {int(k): v for k, v in data.get("nft_owners", {}).items()}
            
            # Clean up: Remove any NFT owners that don't correspond to valid positions
            valid_token_ids = {pos.token_id for pos in self.valid_positions}
            self.nft_owners = {token_id: owner for token_id, owner in self.nft_owners.items() 
                             if token_id in valid_token_ids}
            
            print(f"Loaded data from {file_path}")
            print(f"  Resuming from block: {self.current_block}")
            print(f"  Valid positions: {len(self.valid_positions)}")
            print(f"  Invalid positions: {len(self.invalid_positions)}")
            print(f"  NFT owners: {len(self.nft_owners)} (cleaned up to match valid positions)")
            
        except Exception as e:
            print(f"Error loading data: {e}")
            print("Starting fresh...")

    def _get_logs_internal(self, from_block: int, to_block: int, topics: List[str], address: str) -> List[Dict]:
        """Internal method for getting logs (without retry logic)"""
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_getLogs",
            "params": [{
                "fromBlock": hex(from_block),
                "toBlock": hex(to_block),
                "address": address,
                "topics": topics
            }],
            "id": 1
        }
        
        self.rate_limit_pause()
        response = requests.post(self.rpc_url, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
        
        if "error" in result:
            error_msg = result['error']
            if isinstance(error_msg, dict):
                error_msg = error_msg.get('message', str(error_msg))
            raise Exception(f"RPC Error: {error_msg}")
            
        logs = result.get("result", [])
        if len(logs) >= self.max_logs_per_request:
            print(f"Warning: Received {len(logs)} logs (near limit of {self.max_logs_per_request})")
            
        return logs

    def get_logs(self, from_block: int, to_block: int, topics: List[str], address: str) -> List[Dict]:
        """Get logs using eth_getLogs RPC call with retry logic"""
        try:
            return self.retry_with_backoff(
                self._get_logs_internal, 
                from_block, to_block, topics, address
            )
        except Exception as e:
            print(f"Failed to get logs after {self.max_retries} attempts: {e}")
            return []

    def _call_contract_internal(self, address: str, data: str, block: str = "latest") -> str:
        """Internal method for contract calls (without retry logic)"""
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_call",
            "params": [{
                "to": address,
                "data": data
            }, block],
            "id": 1
        }
        
        self.rate_limit_pause()
        response = requests.post(self.rpc_url, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
        
        if "error" in result:
            error_msg = result['error']
            if isinstance(error_msg, dict):
                error_msg = error_msg.get('message', str(error_msg))
            raise Exception(f"RPC Error: {error_msg}")
            
        return result.get("result")

    def call_contract(self, address: str, data: str, block: str = "latest") -> Optional[str]:
        """Make a contract call using eth_call with retry logic"""
        try:
            return self.retry_with_backoff(
                self._call_contract_internal, 
                address, data, block
            )
        except Exception as e:
            print(f"Failed to call contract after {self.max_retries} attempts: {e}")
            return None

    def get_pool_and_position_info(self, token_id: int) -> Optional[Tuple[PoolKey, int]]:
        """Call getPoolAndPositionInfo for a token ID"""
        # getPoolAndPositionInfo(uint256) selector: 0x7ba03aad
        function_selector = "0x7ba03aad"
        encoded_token_id = hex(token_id)[2:].zfill(64)  # Pad to 32 bytes
        data = function_selector + encoded_token_id
        
        result = self.call_contract(self.nft_address, data)
        if not result:
            return None
            
        try:
            # Decode the result
            # Returns (PoolKey, uint256)
            # PoolKey is (address,address,uint24,int24,address) = 5 * 32 bytes = 160 bytes
            # uint256 is 32 bytes
            # Total expected: 192 bytes of data (384 hex chars after 0x)
            
            result_bytes = bytes.fromhex(result[2:])
            
            # Decode PoolKey struct (5 addresses/uints)
            currency0 = "0x" + result_bytes[12:32].hex()  # address at offset 0
            currency1 = "0x" + result_bytes[44:64].hex()  # address at offset 32
            fee = int.from_bytes(result_bytes[64:96], 'big')  # uint24 at offset 64
            tick_spacing = int.from_bytes(result_bytes[96:128], 'big', signed=True)  # int24 at offset 96
            hooks = "0x" + result_bytes[140:160].hex()  # address at offset 128
            
            # Decode info uint256
            info = int.from_bytes(result_bytes[160:192], 'big')
            
            pool_key = PoolKey(
                currency0=currency0,
                currency1=currency1,
                fee=fee,
                tickSpacing=tick_spacing,
                hooks=hooks
            )
            
            return pool_key, info
            
        except Exception as e:
            print(f"Error decoding getPoolAndPositionInfo result: {e}")
            print(f"Result: {result}")
            return None

    def process_transfer_logs(self, logs: List[Dict]) -> Dict[int, str]:
        """Process Transfer logs and return token_id -> owner mapping"""
        transfers = {}
        processed_count = 0
        
        for log in logs:
            try:
                topics = log.get("topics", [])
                
                if len(topics) >= 4 and topics[0] == self.transfer_topic:
                    from_address = "0x" + topics[1][-40:]  # Last 20 bytes (40 hex chars)
                    to_address = "0x" + topics[2][-40:]    # Last 20 bytes (40 hex chars)
                    token_id = int(topics[3], 16)
                    
                    # Update the owner (transfers can overwrite previous owners)
                    transfers[token_id] = to_address
                    processed_count += 1
                    
            except Exception as e:
                print(f"Error processing Transfer log: {e}")
                continue
        
        if processed_count > 0:
            print(f"      Processed {processed_count} transfer events")
                
        return transfers

    def multicall_get_pool_and_position_info(self, token_ids: List[int]) -> Dict[int, Optional[Tuple[PoolKey, int]]]:
        """
        Use MultiCall3.aggregate3 to get pool and position info for multiple token IDs in a single RPC call
        """
        if not token_ids:
            return {}
        
        # MultiCall3 contract address (deployed on most networks including Base)
        multicall_address = "0xcA11bde05977b3631167028862bE2a173976CA11"
        
        # Create contract instances using Web3.py
        from web3 import Web3
        
        # MultiCall3 ABI for aggregate3 function
        multicall_abi = [
            {
                "inputs": [
                    {
                        "components": [
                            {"internalType": "address", "name": "target", "type": "address"},
                            {"internalType": "bool", "name": "allowFailure", "type": "bool"},
                            {"internalType": "bytes", "name": "callData", "type": "bytes"}
                        ],
                        "internalType": "struct Multicall3.Call3[]",
                        "name": "calls",
                        "type": "tuple[]"
                    }
                ],
                "name": "aggregate3",
                "outputs": [
                    {
                        "components": [
                            {"internalType": "bool", "name": "success", "type": "bool"},
                            {"internalType": "bytes", "name": "returnData", "type": "bytes"}
                        ],
                        "internalType": "struct Multicall3.Result[]",
                        "name": "returnData",
                        "type": "tuple[]"
                    }
                ],
                "stateMutability": "payable",
                "type": "function"
            }
        ]
        
        # Position Manager ABI for the function we need
        position_manager_abi = [
            {
                "inputs": [{"internalType": "uint256", "name": "tokenId", "type": "uint256"}],
                "name": "getPoolAndPositionInfo",
                "outputs": [
                    {
                        "components": [
                            {"internalType": "address", "name": "currency0", "type": "address"},
                            {"internalType": "address", "name": "currency1", "type": "address"},
                            {"internalType": "uint24", "name": "fee", "type": "uint24"},
                            {"internalType": "int24", "name": "tickSpacing", "type": "int24"},
                            {"internalType": "address", "name": "hooks", "type": "address"}
                        ],
                        "internalType": "struct PoolKey",
                        "name": "poolKey",
                        "type": "tuple"
                    },
                    {"internalType": "uint256", "name": "info", "type": "uint256"}
                ],
                "stateMutability": "view",
                "type": "function"
            }
        ]
        
        try:
            # Create contract instances
            multicall_contract = self.web3.eth.contract(address=multicall_address, abi=multicall_abi)
            position_contract = self.web3.eth.contract(address=self.nft_address, abi=position_manager_abi)
            
            # Prepare calls for aggregate3
            calls = []
            for token_id in token_ids:
                # Try different methods to encode the function call
                call_data = None
                
                # Method 1: Try newer Web3.py approach
                try:
                    call_data = position_contract.encode_abi('getPoolAndPositionInfo', [token_id])
                except (AttributeError, TypeError):
                    pass
                
                # Method 2: Try older Web3.py approach  
                if call_data is None:
                    try:
                        call_data = position_contract.encodeABI(fn_name='getPoolAndPositionInfo', args=[token_id])
                    except (AttributeError, TypeError):
                        pass
                
                # Method 3: Try direct function call encoding
                if call_data is None:
                    try:
                        call_data = position_contract.get_function_by_name('getPoolAndPositionInfo')(token_id).buildTransaction({
                            'to': self.nft_address,
                            'from': '0x0000000000000000000000000000000000000000'
                        })['data']
                    except (AttributeError, TypeError):
                        pass
                
                # Method 4: Manual encoding as last resort
                if call_data is None:
                    function_selector = "0x7ba03aad"  # getPoolAndPositionInfo(uint256)
                    encoded_token_id = hex(token_id)[2:].zfill(64)  # Pad to 32 bytes
                    call_data = function_selector + encoded_token_id
                
                calls.append({
                    'target': self.nft_address,
                    'allowFailure': True,  # Allow individual calls to fail
                    'callData': call_data
                })
            
            print(f"Making MultiCall3.aggregate3 for {len(token_ids)} positions...")
              
            # Add delay before multicall to avoid overwhelming RPC
            time.sleep(2)
            
            # Make the multicall using aggregate3
            results = multicall_contract.functions.aggregate3(calls).call()
            
            # Process results
            final_results = {}
            successful_count = 0
            
            for i, (token_id, result) in enumerate(zip(token_ids, results)):
                try:
                    success, return_data = result
                    
                    if not success or return_data == b'':
                        print(f"Call failed for token {token_id}")
                        final_results[token_id] = None
                        continue
                    
                    # Decode the result using the contract interface
                    try:
                        decoded = position_contract.decode_function_result('getPoolAndPositionInfo', return_data)
                        pool_key_tuple, info = decoded
                        
                        # Extract PoolKey fields
                        pool_key = PoolKey(
                            currency0=pool_key_tuple[0],  # currency0
                            currency1=pool_key_tuple[1],  # currency1  
                            fee=pool_key_tuple[2],        # fee
                            tickSpacing=pool_key_tuple[3], # tickSpacing
                            hooks=pool_key_tuple[4]       # hooks
                        )
                        
                        final_results[token_id] = (pool_key, info)
                        successful_count += 1
                        
                    except Exception as decode_error:
                        print(f"Standard decoding failed for token {token_id}, trying manual decode...")
                        
                        # Manual decoding fallback
                        try:
                            # Try multiple approaches for different Web3.py versions
                            decoded_values = None
                            
                            # Method 1: Try eth_abi directly
                            try:
                                from eth_abi import decode
                                decoded_values = decode(
                                    ['address', 'address', 'uint24', 'int24', 'address', 'uint256'],
                                    return_data
                                )
                            except ImportError:
                                pass
                            
                            # Method 2: Try Web3's codec if eth_abi not available
                            if decoded_values is None:
                                try:
                                    # For newer Web3.py versions
                                    decoded_values = self.web3.codec.decode(
                                        ['address', 'address', 'uint24', 'int24', 'address', 'uint256'],
                                        return_data
                                    )
                                except AttributeError:
                                    pass
                            
                            # Method 3: Manual byte parsing as last resort
                            if decoded_values is None:
                                print(f"Using manual byte parsing for token {token_id}")
                                # Parse the 192 bytes manually
                                currency0 = "0x" + return_data[12:32].hex()
                                currency1 = "0x" + return_data[44:64].hex() 
                                fee = int.from_bytes(return_data[64:96], 'big')
                                tick_spacing = int.from_bytes(return_data[96:128], 'big', signed=True)
                                hooks = "0x" + return_data[140:160].hex()
                                info = int.from_bytes(return_data[160:192], 'big')
                                
                                decoded_values = [currency0, currency1, fee, tick_spacing, hooks, info]
                            
                            if decoded_values:
                            
                                pool_key = PoolKey(
                                    currency0=decoded_values[0],
                                    currency1=decoded_values[1],
                                    fee=decoded_values[2],
                                    tickSpacing=decoded_values[3],
                                    hooks=decoded_values[4]
                                )
                            
                                info = decoded_values[5]
                                final_results[token_id] = (pool_key, info)
                                successful_count += 1
                            
                        except Exception as manual_error:
                            print(f"Manual decoding also failed for token {token_id}: {manual_error}")
                            print(f"Raw data length: {len(return_data)} bytes")
                            final_results[token_id] = None
                    
                except Exception as e:
                    print(f"Error processing result for token {token_id}: {e}")
                    final_results[token_id] = None
            
            print(f"MultiCall3 successful: {successful_count}/{len(token_ids)} positions decoded")
            return final_results
            
        except Exception as e:
            print(f"MultiCall3 failed: {e}")
            print("Falling back to individual calls")
            return self._fallback_individual_calls(token_ids)

    def _fallback_individual_calls(self, token_ids: List[int]) -> Dict[int, Optional[Tuple[PoolKey, int]]]:
        """Fallback to individual calls if MultiCall fails"""
        results = {}
        batch_size = 5
        
        print(f"Making individual calls for {len(token_ids)} positions...")
        
        for i in range(0, len(token_ids), batch_size):
            batch = token_ids[i:i + batch_size]
            print(f"  Processing batch {i//batch_size + 1}/{(len(token_ids) + batch_size - 1)//batch_size}")
            
            for token_id in batch:
                try:
                    results[token_id] = self.get_pool_and_position_info(token_id)
                except Exception as e:
                    print(f"    Individual call failed for token {token_id}: {e}")
                    results[token_id] = None
                
                # Rate limiting
                time.sleep(0.2)
            
            # Pause between batches
            if i + batch_size < len(token_ids):
                time.sleep(2)
        
        successful = len([r for r in results.values() if r is not None])
        print(f"Individual calls complete: {successful}/{len(token_ids)} positions retrieved")
        return results

    def validate_positions(self, position_data: List[Tuple[int, str, int]]) -> Tuple[List[Position], List[Position]]:
        """Validate positions against target pool key using MultiCall3 for efficiency"""
        valid_positions = []
        invalid_positions = []
        
        if not position_data:
            return valid_positions, invalid_positions
        
        # Extract token IDs for batch call
        token_ids = [token_id for token_id, _, _ in position_data]
        
        print(f"Validating {len(token_ids)} positions using MultiCall3...")
        
        # Get all position info using MultiCall3
        position_info_results = self.multicall_get_pool_and_position_info(token_ids)
        
        # Process results
        for token_id, tx_hash, block_number in position_data:
            try:
                result = position_info_results.get(token_id)
                if not result:
                    print(f"Could not get pool info for token {token_id}")
                    continue
                    
                pool_key, info = result
                
                print(f"\nToken {token_id}:")
                print(f"  Currency0: {pool_key.currency0}")
                print(f"  Currency1: {pool_key.currency1}")
                print(f"  Fee: {pool_key.fee}")
                print(f"  TickSpacing: {pool_key.tickSpacing}")
                print(f"  Hooks: {pool_key.hooks}")
                print(f"  Info: {info}")
                
                owner = self.nft_owners.get(token_id, "Unknown")
                timestamp = datetime.now().isoformat()
                
                position = Position(
                    token_id=token_id,
                    pool_key=pool_key,
                    owner=owner,
                    block_number=block_number,
                    tx_hash=tx_hash,
                    timestamp=timestamp
                )
                
                if pool_key.matches_target(self.target_pool_key):
                    print(f"  ✓ VALID - matches target pool")
                    valid_positions.append(position)
                else:
                    print(f"  ✗ INVALID - does not match target pool")
                    invalid_positions.append(position)
                    
            except Exception as e:
                print(f"Error validating token {token_id}: {e}")
                continue
        
        print(f"Validation complete: {len(valid_positions)} valid, {len(invalid_positions)} invalid")
        return valid_positions, invalid_positions

    def batch_validate_positions(self, position_data: List[Tuple[int, str, int]], batch_size: int = 20) -> Tuple[List[Position], List[Position]]:
        """
        Validate positions in batches to avoid overwhelming the RPC endpoint
        """
        all_valid = []
        all_invalid = []
        
        # Process in batches
        for i in range(0, len(position_data), batch_size):
            batch = position_data[i:i + batch_size]
            print(f"Processing validation batch {i//batch_size + 1}/{(len(position_data) + batch_size - 1)//batch_size}")
            
            valid, invalid = self.validate_positions(batch)
            all_valid.extend(valid)
            all_invalid.extend(invalid)
            
            # Delay between batches to be nice to RPC
            if i + batch_size < len(position_data):
                print("Pausing between validation batches...")
                time.sleep(3)
        
        return all_valid, all_invalid

    def calculate_block_range(self, from_block: int, to_block: int) -> List[Tuple[int, int]]:
        """Calculate block ranges to stay within log and block limits"""
        ranges = []
        current = from_block
            
        while current <= to_block:
                # Use the smaller of max_blocks_per_request or remaining blocks
            end_block = min(current + self.max_blocks_per_request - 1, to_block)
            ranges.append((current, end_block))
            current = end_block + 1
                
        return ranges

    def get_newly_minted_positions(self, transfer_logs):
        """Extract token IDs that were minted (transferred from zero address)"""
        newly_minted = []
        zero_address = "0x0000000000000000000000000000000000000000000000000000000000000000"
            
        for log in transfer_logs:
            # Transfer event structure: Transfer(address indexed from, address indexed to, uint256 indexed tokenId)
            # topics[0] = event signature
            # topics[1] = from address
            # topics[2] = to address
            # topics[3] = tokenId
                
            if len(log['topics']) >= 4:
                from_address = log['topics'][1]
                token_id = int(log['topics'][3], 16)  # Convert hex to int
                print(f"From Address: {from_address}")
                # Check if transfer is from zero address (minting)
                if from_address.lower() == zero_address.lower():
                    newly_minted.append(token_id)
                    print(f"  Found newly minted token: {token_id}")
            
        return newly_minted

    def scan_blocks(self, from_block: int, to_block: int):
        """Scan a range of blocks for events"""
        print(f"\nScanning blocks {from_block} to {to_block}...")
            
        # Split into smaller ranges if needed
        block_ranges = self.calculate_block_range(from_block, to_block)
            
        all_new_positions = []
        all_transfers = {}
            
        for start, end in block_ranges:
            print(f"  Scanning sub-range: {start} to {end} ({end - start + 1} blocks)")
                
            # Get Transfer logs with retry
            transfer_logs = self.get_logs(
                from_block=start,
                to_block=end,
                topics=[self.transfer_topic],
                address=self.nft_address
            )
                
            print(f"    Found {len(transfer_logs)} Transfer events")
                
            if transfer_logs:
                # Process all transfers
                transfers = self.process_transfer_logs(transfer_logs)
                all_transfers.update(transfers)
                    
                # Identify new position creations (transfers from zero address)
                new_position_token_ids = self.get_newly_minted_positions(transfer_logs)
                    
                if new_position_token_ids:
                    print(f"    Found {len(new_position_token_ids)} newly minted positions")
                        
                    # Validate new positions using the MultiCall implementation
                    # Convert token_ids to position_data format for validate_positions
                    position_data = []
                    for token_id in new_position_token_ids:
                        # Extract tx_hash from the transfer logs for this token_id
                        tx_hash = ""
                        for log in transfer_logs:
                            if (len(log.get('topics', [])) >= 4 and 
                                int(log['topics'][3], 16) == token_id and
                                log['topics'][1].lower() == "0x0000000000000000000000000000000000000000000000000000000000000000"):
                                tx_hash = log.get('transactionHash', '')
                                break
                            
                        position_data.append((token_id, tx_hash, end))
                        
                    # Validate positions using MultiCall
                    valid, invalid = self.validate_positions(position_data)
                        
                    # Add to our collections
                    self.valid_positions.extend(valid)
                    self.invalid_positions.extend(invalid)
                        
                    # Track these as new positions found
                    all_new_positions.extend(new_position_token_ids)
                        
                    print(f"    Validation results: {len(valid)} valid, {len(invalid)} invalid positions")
            
        # Update NFT ownership for all transfers
        if all_transfers:
            print(f"  Updating NFT ownership for {len(all_transfers)} transfers")
            self.nft_owners.update(all_transfers)
                
            # Update ownership info in valid positions
            updated_count = 0
            for position in self.valid_positions:
                if position.token_id in all_transfers:
                    old_owner = position.owner
                    position.owner = all_transfers[position.token_id]
                    if old_owner != position.owner:
                        updated_count += 1
                        print(f"    Updated owner for token {position.token_id}: {old_owner} -> {position.owner}")
                
            if updated_count > 0:
                print(f"  Updated ownership for {updated_count} valid positions")
            
        # Summary for this block range
        if all_new_positions:
            print(f"  Block range summary: {len(all_new_positions)} new positions discovered")
            
        return all_new_positions

    def _get_latest_block_internal(self) -> int:
        """Internal method for getting latest block (without retry logic)"""
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_blockNumber",
            "params": [],
            "id": 1
        }
        
        self.rate_limit_pause()
        response = requests.post(self.rpc_url, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
        
        if "error" in result:
            error_msg = result['error']
            if isinstance(error_msg, dict):
                error_msg = error_msg.get('message', str(error_msg))
            raise Exception(f"RPC Error: {error_msg}")
            
        return int(result.get("result", "0x0"), 16)

    def get_latest_block(self) -> int:
        """Get the latest block number with retry logic"""
        try:
            return self.retry_with_backoff(self._get_latest_block_internal)
        except Exception as e:
            print(f"Failed to get latest block after {self.max_retries} attempts: {e}")
            return self.current_block

    def print_summary(self):
        """Print a summary of findings"""
        print(f"\n{'='*50}")
        print("SUMMARY")
        print(f"{'='*50}")
        print(f"Blocks scanned: {self.start_block} to {self.current_block}")
        print(f"Valid positions: {len(self.valid_positions)}")
        print(f"Invalid positions: {len(self.invalid_positions)}")
        print(f"NFT owners tracked: {len(self.nft_owners)}")
        
        if self.valid_positions:
            print(f"\nVALID POSITIONS:")
            for pos in self.valid_positions:
                print(f"  Token ID: {pos.token_id}, Owner: {pos.owner}, Block: {pos.block_number}")
        else:
            print(f"\nNo valid positions found yet")
        
        if self.nft_owners:
            print(f"\nVALID NFT OWNERS (tracked):")
            for token_id, owner in self.nft_owners.items():
                print(f"  Token ID: {token_id}, Owner: {owner}")
        else:
            print(f"No valid NFT owners being tracked")

    def run_once(self, blocks_per_scan: int = 1000, max_total_blocks: int = 50000):
        """Run scan cycles until caught up to latest block or max_total_blocks reached"""
        latest_block = self.get_latest_block()
        total_blocks_scanned = 0
        
        print(f"Starting scan. Current block: {self.current_block}, Latest block: {latest_block}")
        
        while self.current_block <= latest_block and total_blocks_scanned < max_total_blocks:
            # Calculate how many blocks to scan in this iteration
            remaining_blocks = latest_block - self.current_block + 1
            blocks_to_scan = min(blocks_per_scan, remaining_blocks, max_total_blocks - total_blocks_scanned)
            to_block = self.current_block + blocks_to_scan - 1
            
            print(f"\nIteration: Current {self.current_block}, Scanning to {to_block} ({blocks_to_scan} blocks)")
            
            self.scan_blocks(self.current_block, to_block)
            self.current_block = to_block + 1
            total_blocks_scanned += blocks_to_scan
            
            # Auto-save after each iteration
            self.save_data()
            
            # Check if we've reached the latest block
            if self.current_block > latest_block:
                print(f"\nCaught up to block {latest_block}")
                break
                
            # Small pause between iterations to avoid overwhelming RPC
            if self.current_block <= latest_block:
                print("Pausing before next iteration...")
                time.sleep(2)
        
        if total_blocks_scanned >= max_total_blocks:
            print(f"\nReached max scan limit of {max_total_blocks} blocks")
            print(f"Still {latest_block - self.current_block + 1} blocks behind")
        
        self.print_summary()
        return latest_block
        

    def run_continuous(self, blocks_per_scan: int = 1000, sleep_seconds: int = 10):
        """Run continuous monitoring - always scan to the newest block"""
        print("Starting continuous monitoring...")
        print("Will continuously scan to the newest block")
        print("Press Ctrl+C to stop")
        
        try:
            while True:
                latest_block = self.get_latest_block()
                
                if self.current_block <= latest_block:
                    # There are new blocks to scan
                    remaining_blocks = latest_block - self.current_block + 1
                    print(f"\n{remaining_blocks} blocks behind latest ({self.current_block} → {latest_block})")
                    
                    # Scan in chunks if there are many blocks to catch up
                    while self.current_block <= latest_block:
                        # Re-get latest block in case it changed during scanning
                        current_latest = self.get_latest_block()
                        blocks_to_scan = min(blocks_per_scan, current_latest - self.current_block + 1)
                        to_block = self.current_block + blocks_to_scan - 1
                        
                        if blocks_to_scan > 0:
                            print(f"Scanning blocks {self.current_block} to {to_block} ({blocks_to_scan} blocks)")
                            self.scan_blocks(self.current_block, to_block)
                            self.current_block = to_block + 1
                            
                            # Auto-save after each chunk
                            self.save_data()
                            
                            # Brief pause between chunks to avoid overwhelming RPC
                            if self.current_block <= current_latest:
                                time.sleep(2)
                        else:
                            break
                    
                    self.print_summary()
                    print(f"✓ Caught up to block {latest_block}")
                else:
                    print(f"Up to date at block {latest_block}")
                
                # Wait before checking for new blocks
                print(f"Waiting {sleep_seconds}s before checking for new blocks...")
                time.sleep(sleep_seconds)
                        
        except KeyboardInterrupt:
            print("\nStopping monitor...")
            self.save_data()
            self.print_summary()

def main():
    # Configuration - get from environment or use defaults
    RPC_URL = os.environ.get('RPC_URL')
    if not RPC_URL:
        RPC_URL = "https://mainnet.base.org"
        print(f"No RPC_URL environment variable found, using default RPC endpoint")
    else:
        print(f"Using RPC_URL from environment variable")
    
    START_BLOCK = 35956776
    SAVE_FILE = "mainnet_uniswap_v4_data.json"
    
    print(f"Connecting to RPC: {RPC_URL[:50]}...")
    
    # Test connection first
    try:
        web3 = Web3(Web3.HTTPProvider(RPC_URL))
        latest_block = web3.eth.get_block('latest')
        print(f"Successfully connected to RPC. Latest block: {latest_block['number']}")
    except Exception as e:
        print(f"Failed to connect to RPC: {e}")
        exit(1)
    
    # Create the monitor
    monitor = UniswapV4Monitor(RPC_URL, START_BLOCK, SAVE_FILE)
    
    # For GitHub Actions, run once with a reasonable block limit
    print("Running single scan for GitHub Actions...")
    try:
        # Scan up to 2000 blocks in one run to avoid timeout
        monitor.run_once(blocks_per_scan=2000)
        print("GitHub Actions scan completed successfully!")
    except Exception as e:
        print(f"Error during GitHub Actions scan: {e}")
        # Save whatever progress was made
        monitor.save_data()
        exit(1)

if __name__ == "__main__":
    main()
