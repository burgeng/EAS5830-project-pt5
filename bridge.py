from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
import json
import os


STATE_FILE = "bridge_state.json"
BLOCK_CHUNK_SIZE = 1
INITIAL_LOOKBACK = 25
MAX_CATCHUP_BLOCKS = 50


def connect_to(chain):
    if chain == "source":
        api_url = "https://api.avax-test.network/ext/bc/C/rpc"
    elif chain == "destination":
        api_url = "https://data-seed-prebsc-1-s1.binance.org:8545/"
    else:
        raise ValueError(f"Invalid chain: {chain}")

    w3 = Web3(Web3.HTTPProvider(api_url))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return w3


def get_contract_info(chain, contract_info):
    try:
        with open(contract_info, "r") as f:
            contracts = json.load(f)
    except Exception as e:
        print(f"Failed to read contract info\nPlease contact your instructor\n{e}")
        return 0
    return contracts[chain]


def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            "last_scanned_source": None,
            "last_scanned_destination": None,
        }

    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {
            "last_scanned_source": None,
            "last_scanned_destination": None,
        }


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def get_logs_chunked(event_obj, start_block, end_block, chunk_size=BLOCK_CHUNK_SIZE):
    all_events = []
    current = start_block

    while current <= end_block:
        chunk_end = min(current + chunk_size - 1, end_block)

        try:
            events = event_obj.get_logs(
                from_block=current,
                to_block=chunk_end
            )
        except TypeError:
            events = event_obj.get_logs(
                fromBlock=current,
                toBlock=chunk_end
            )

        all_events.extend(events)
        current = chunk_end + 1

    return all_events


def choose_scan_range(last_scanned, current_block):
    """
    Pick a sane scan window.

    - First run: only look back a small recent window.
    - Normal run: continue from last_scanned + 1.
    - If saved state is stale / too old, snap to a recent catchup window.
    """
    if last_scanned is None:
        start_block = max(0, current_block - INITIAL_LOOKBACK)
        end_block = current_block
        return start_block, end_block

    if last_scanned >= current_block:
        return current_block + 1, current_block

    proposed_start = last_scanned + 1

    # If the gap is too large, do not scan thousands of old blocks.
    if current_block - proposed_start > MAX_CATCHUP_BLOCKS:
        start_block = max(0, current_block - INITIAL_LOOKBACK)
        end_block = current_block
        return start_block, end_block

    return proposed_start, current_block


def scan_blocks(chain, contract_info="contract_info.json"):
    if chain not in ["source", "destination"]:
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
        abi=source_info["abi"],
    )

    destination_contract = destination_w3.eth.contract(
        address=Web3.to_checksum_address(destination_info["address"]),
        abi=destination_info["abi"],
    )

    state = load_state()

    def send_tx(w3, fn):
        nonce = w3.eth.get_transaction_count(acct.address, "pending")

        tx = fn.build_transaction({
            "from": acct.address,
            "nonce": nonce,
            "gas": 500000,
            "gasPrice": w3.eth.gas_price,
            "chainId": w3.eth.chain_id,
        })

        signed = acct.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        return receipt

    if chain == "source":
        current_block = source_w3.eth.block_number
        last_scanned = state.get("last_scanned_source")

        start_block, end_block = choose_scan_range(last_scanned, current_block)

        if start_block > end_block:
            print(f"No new source blocks to scan ({start_block} > {end_block})")
            return 1

        print(f"Scanning source blocks {start_block} to {end_block}")

        events = get_logs_chunked(
            source_contract.events.Deposit(),
            start_block,
            end_block
        )

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

        state["last_scanned_source"] = end_block
        save_state(state)

    elif chain == "destination":
        current_block = destination_w3.eth.block_number
        last_scanned = state.get("last_scanned_destination")

        start_block, end_block = choose_scan_range(last_scanned, current_block)

        if start_block > end_block:
            print(f"No new destination blocks to scan ({start_block} > {end_block})")
            return 1

        print(f"Scanning destination blocks {start_block} to {end_block}")

        events = get_logs_chunked(
            destination_contract.events.Unwrap(),
            start_block,
            end_block
        )

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

        state["last_scanned_destination"] = end_block
        save_state(state)

    return 1