# B0x Mining Data - Base Mainnet

This directory contains automatically updated mining data for the B0x token on Base mainnet.

## Files

- mined_blocks_mainnet.json - Main data file with all mining transactions
- latest.json - Copy of the main file for easy access
- index.html - Web interface for viewing the data

## Direct Access Links

- JSON Data: https://raw.githubusercontent.com/B0x-Token/B0x_Scripts_auto_2/main/mainnetB0x/mined_blocks_mainnet.json
- Latest Copy: https://raw.githubusercontent.com/B0x-Token/B0x_Scripts_auto_2/main/mainnetB0x/latest.json

## Data Structure

The JSON file contains an object with the following structure:
- mined_blocks: Array of arrays containing [block_number, tx_hash, miner_address, amount, epoch_count]
- total_blocks: Total number of blocks processed
- last_updated: Unix timestamp of last update
- latest_block_number: Latest blockchain block processed
- contract_address: The smart contract address
- mint_topic: The event topic hash
- previous_challenge: Last challenge hash

Updated automatically every 30 minutes via GitHub Actions.
