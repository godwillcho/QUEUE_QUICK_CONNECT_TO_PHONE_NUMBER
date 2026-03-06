import boto3
import csv
import re
import os
import logging
from datetime import datetime

# =============================================================================
# CONFIGURATION
# =============================================================================
TABLE_NAME = 'connect-queue-metadata-dev'
AWS_REGION = 'us-east-1'
DRY_RUN = True  # Set to False to actually write changes

# DynamoDB attributes
PK_ATTRIBUTE = 'PhoneNumberE164'
MATCH_ATTRIBUTE = 'QueueName'

# CSV file (same directory as this script)
CSV_FILE = 'queue_phones.csv'
CSV_MATCH_COLUMN = 'Queue Name'        # Matches against DynamoDB QueueName
CSV_PHONE_COLUMN = 'External Number'   # Phone number to convert to E.164 for new PK

# Log file (same directory as this script)
LOG_FILE = 'update_dynamodb_pk.log'


# =============================================================================
# LOGGING SETUP
# =============================================================================
def setup_logging():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    log_path = os.path.join(script_dir, LOG_FILE)

    logger = logging.getLogger('pk_updater')
    logger.setLevel(logging.INFO)

    file_handler = logging.FileHandler(log_path, mode='w')
    file_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter('[%(levelname)s] %(message)s'))

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


# =============================================================================
# E.164 CONVERSION
# =============================================================================
def to_e164(phone):
    cleaned = re.sub(r'[^\d+]', '', phone)
    if cleaned.startswith('+'):
        return cleaned
    elif len(cleaned) == 10:
        return f"+1{cleaned}"
    elif len(cleaned) == 11 and cleaned.startswith('1'):
        return f"+{cleaned}"
    else:
        return None


# =============================================================================
# MAIN
# =============================================================================
def main():
    log = setup_logging()
    log.info(f"Started at {datetime.now().isoformat()}")
    log.info(f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE'}")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    csv_file = os.path.join(script_dir, CSV_FILE)

    if not os.path.exists(csv_file):
        log.error(f"CSV file not found: {csv_file}")
        log.error(f"Place '{CSV_FILE}' in the same directory as this script.")
        return

    # Step 1: Read CSV into lookup {QueueName: E.164 phone}
    csv_lookup = {}
    with open(csv_file, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            queue_name = row[CSV_MATCH_COLUMN].strip()
            phone_raw = row[CSV_PHONE_COLUMN].strip()
            phone_e164 = to_e164(phone_raw)
            if not phone_e164:
                log.warning(f"Could not convert phone '{phone_raw}' for queue '{queue_name}', skipping")
                continue
            csv_lookup[queue_name] = phone_e164

    log.info(f"Loaded {len(csv_lookup)} entries from CSV")

    # Step 2: Scan DynamoDB table
    dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
    table = dynamodb.Table(TABLE_NAME)

    items = []
    scan_kwargs = {}
    while True:
        response = table.scan(**scan_kwargs)
        items.extend(response.get('Items', []))
        if 'LastEvaluatedKey' not in response:
            break
        scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']

    log.info(f"Found {len(items)} items in DynamoDB table '{TABLE_NAME}'")

    # Step 3: Match and update
    updated_items = []
    skipped_items = []
    failed_items = []
    unmatched_items = []

    for item in items:
        queue_name = item.get(MATCH_ATTRIBUTE, '')
        old_pk = item.get(PK_ATTRIBUTE, '')

        if queue_name not in csv_lookup:
            unmatched_items.append({'QueueName': queue_name, 'CurrentPK': old_pk})
            continue

        new_pk = csv_lookup[queue_name]

        if old_pk == new_pk:
            skipped_items.append({'QueueName': queue_name, 'PK': new_pk, 'Reason': 'Already correct'})
            continue

        # Build new item with updated PK, all other attributes unchanged
        new_item = dict(item)
        new_item[PK_ATTRIBUTE] = new_pk

        if not DRY_RUN:
            try:
                table.delete_item(Key={PK_ATTRIBUTE: old_pk})
                table.put_item(Item=new_item)
                updated_items.append({'QueueName': queue_name, 'OldPK': old_pk, 'NewPK': new_pk})
            except Exception as e:
                failed_items.append({'QueueName': queue_name, 'OldPK': old_pk, 'NewPK': new_pk, 'Error': str(e)})
        else:
            updated_items.append({'QueueName': queue_name, 'OldPK': old_pk, 'NewPK': new_pk})

    # Step 4: Log all results
    if updated_items:
        log.info(f"--- Updated ({len(updated_items)}) ---")
        for entry in updated_items:
            log.info(f"  {entry['QueueName']}: {entry['OldPK']} -> {entry['NewPK']}")

    if skipped_items:
        log.info(f"--- Skipped ({len(skipped_items)}) ---")
        for entry in skipped_items:
            log.info(f"  {entry['QueueName']}: PK already {entry['PK']}")

    if unmatched_items:
        log.warning(f"--- Unmatched ({len(unmatched_items)}) ---")
        for entry in unmatched_items:
            log.warning(f"  {entry['QueueName']}: CurrentPK={entry['CurrentPK']} - No matching row in CSV")

    if failed_items:
        log.error(f"--- Failed ({len(failed_items)}) ---")
        for entry in failed_items:
            log.error(f"  {entry['QueueName']}: {entry['OldPK']} -> {entry['NewPK']} | Error: {entry['Error']}")

    # Step 5: Summary
    log.info("--- Summary ---")
    log.info(f"Mode:      {'DRY RUN' if DRY_RUN else 'LIVE'}")
    log.info(f"Updated:   {len(updated_items)}")
    log.info(f"Skipped:   {len(skipped_items)}")
    log.info(f"Unmatched: {len(unmatched_items)}")
    log.info(f"Failed:    {len(failed_items)}")

    if DRY_RUN and updated_items:
        log.info("Set DRY_RUN = False in the script to apply changes.")

    log.info(f"Log saved to {LOG_FILE}")


if __name__ == '__main__':
    main()
