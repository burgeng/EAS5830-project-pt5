from web3 import Web3
from web3.providers.rpc import HTTPProvider
from web3.middleware import ExtraDataToPOAMiddleware  # Necessary for POA chains
from datetime import datetime
import json
import pandas as pd


def connect_to(chain):
    if chain == 'source':  # The source contract chain is avax
        api_url = "https://api.avax-test.network/ext/bc/C/rpc"  # AVAX C-chain testnet
    elif chain == 'destination':  # The destination contract chain is bsc
        api_url = "https://data-seed-prebsc-1-s1.binance.org:8545/"  # BSC testnet
    else:
        raise ValueError(f"Invalid chain: {chain}")

    w3 = Web3(Web3.HTTPProvider(api_url))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return w3


def get_contract_info(chain, contract_info):
    """
        Load the contract_info file into a dictionary
        This function is used by the autograder and will likely be useful to you
    """
    try:
        with open(contract_info, 'r') as f:
            contracts = json.load(f)
    except Exception as e:
        print(f"Failed to read contract info\nPlease contact your instructor\n{e}")
        return 0
    return contracts[chain]


def scan_blocks(chain, contract_info="contract_info.json"):
    """
        chain - (string) should be either "source" or "destination"
        Scan the last 5 blocks of the source and destination chains
        Look for 'Deposit' events on the source chain and 'Unwrap' events on the destination chain
        When Deposit events are found on the source chain, call the 'wrap' function the destination chain
        When Unwrap events are found on the destination chain, call the 'withdraw' function on the source chain
    """

    if chain not in ['source', 'destination']:
        print(f"Invalid chain: {chain}")
        return 0

    try:
        with open(contract_info, "r") as f:
            info = json.load(f)
    except Exception as e:
        print(f"Failed to read contract info: {e}")
        return 0

    private_key = info["warden"]["private_key"]
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key

    acct = Web3().eth.account.from_key(private_key)

    source_w3 = connect_to("source")
    destination_w3 = connect_to("destination")

    source_info = info["source"]
    destination_info = info["destination"]

    source_contract = source_w3.eth.contract(
        address=Web3.to_checksum_address(source_info["address"]),
        abi=source_info["abi"]
    )

    destination_contract = destination_w3.eth.contract(
        address=Web3.to_checksum_address(destination_info["address"]),
        abi=destination_info["abi"]
    )

    def send_tx(w3, fn):
        nonce = w3.eth.get_transaction_count(acct.address)

        tx = fn.build_transaction({
            "from": acct.address,
            "nonce": nonce,
            "gas": 500000,
            "gasPrice": w3.eth.gas_price,
            "chainId": w3.eth.chain_id
        })

        signed = acct.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        return receipt

    if chain == "source":
        end_block = source_w3.eth.block_number
        start_block = max(0, end_block - 5)

        print(f"Scanning source blocks {start_block} to {end_block}")

        event_filter = source_contract.events.Deposit.create_filter(
            from_block=start_block,
            to_block=end_block
        )
        events = event_filter.get_all_entries()

        for evt in events:
            token = evt["args"]["token"]
            recipient = evt["args"]["recipient"]
            amount = evt["args"]["amount"]

            print(f"Found Deposit event: {evt['transactionHash'].hex()}")

            receipt = send_tx(
                destination_w3,
                destination_contract.functions.wrap(token, recipient, amount)
            )

            print(f"Called wrap() on destination: {receipt.transactionHash.hex()}")

    elif chain == "destination":
        end_block = destination_w3.eth.block_number
        start_block = max(0, end_block - 5)

        print(f"Scanning destination blocks {start_block} to {end_block}")

        event_filter = destination_contract.events.Unwrap.create_filter(
            from_block=start_block,
            to_block=end_block
        )
        events = event_filter.get_all_entries()

        for evt in events:
            underlying_token = evt["args"]["underlying_token"]
            recipient = evt["args"]["to"]
            amount = evt["args"]["amount"]

            print(f"Found Unwrap event: {evt['transactionHash'].hex()}")

            receipt = send_tx(
                source_w3,
                source_contract.functions.withdraw(underlying_token, recipient, amount)
            )

            print(f"Called withdraw() on source: {receipt.transactionHash.hex()}")

    return 1