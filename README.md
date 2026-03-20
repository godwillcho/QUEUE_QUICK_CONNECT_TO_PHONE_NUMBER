# Amazon Connect Queue & Quick Connect Provisioning System

Automated provisioning of Amazon Connect queues and quick connects from CSV files using AWS CloudFormation. Supports transferring to external phone numbers or to other Connect queues, with per-transfer custom Caller IDs.

## Why Not Use Phone Quick Connects?

Amazon Connect provides a built-in **Phone quick connect** type that transfers directly to an external number. However, it has two key limitations that this solution addresses:

### 1. No Custom Caller ID Per Transfer

With Phone quick connects, **you cannot set a custom outbound Caller ID per transfer**. The caller ID is determined by the queue's outbound caller ID configuration and applies to all transfers from that queue — you cannot show a different number to the receiving party based on which quick connect was clicked.

This solution uses **Queue quick connects paired with a contact flow** instead:

1. Agent clicks a Queue quick connect (e.g., "Sales")
2. A contact flow fires and looks up the record in DynamoDB
3. Retrieves the `CallerIdE164` specific to that transfer (e.g., `+15550000001`)
4. Sets it as the outbound caller ID before transferring the call

**Each row in the CSV has its own `CallerID` field**, so different transfers can present different whitelisted phone numbers to the receiving party. This is not achievable with native Phone quick connects.

| Quick Connect | Destination | Caller ID Shown |
|---------------|-------------|-----------------|
| Sales | +1 (555) 123-4567 | +1 (555) 000-0001 |
| Support | +1 (555) 987-6543 | +1 (555) 000-0002 |
| Billing | 1-800-555-0100 | +1 (800) 000-0003 |

Each transfer shows a **different outbound number** to the person being called, even though agents are all in the same queue. The Caller ID numbers must be whitelisted (claimed) in your Amazon Connect instance.

### 2. Hiding External Phone Numbers From Agents

With a Phone quick connect, **the external phone number is visible to the agent** in the Contact Control Panel (CCP). This can be a problem when the business requires that agents should not know the actual destination numbers — for example, when transferring to partner organizations, external vendors, or sensitive departments where the direct number must remain confidential.

Because this solution uses **Queue quick connects**, agents only see the **queue name** (e.g., "Sales", "Partner Support") in their CCP — not the underlying phone number. The phone number is stored in DynamoDB and resolved at runtime by the contact flow, completely hidden from the agent's view.

## Architecture

```
CSV Upload to S3
       │
       ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Provisioning Lambda                                                 │
│                                                                      │
│  For each batch of 10 rows:                                          │
│    1. Create queues in Amazon Connect                                │
│    2. Wait for propagation                                           │
│    3. Create queue quick connects                                    │
│    4. Associate quick connects to target queues                      │
│    5. Save metadata to DynamoDB                                      │
│    6. Pause between batches (rate limiting)                          │
│                                                                      │
│  Write results:                                                      │
│    results/{timestamp}_{csv-name}/success.csv                        │
│    results/{timestamp}_{csv-name}/failure.csv                        │
└──────────┬───────────────┬──────────────────┬────────────────────────┘
           │               │                  │
           ▼               ▼                  ▼
    ┌────────────┐  ┌────────────┐   ┌──────────────────┐
    │  DynamoDB   │  │  Connect   │   │  Connect Quick   │
    │  Metadata   │  │  Queues    │   │  Connects        │
    └──────┬─────┘  └────────────┘   └──────────────────┘
           │
           ▼
    ┌────────────┐         ┌──────────────────────────┐
    │  Query     │◀────────│  Queue Transfer Flow     │
    │  Lambda    │         │                          │
    │            │         │  PHONE → external call   │
    └────────────┘         │  QUEUE → queue transfer  │
                           └──────────────────────────┘
```

## What Gets Created

| Resource | Name Pattern | Purpose |
|----------|-------------|---------|
| DynamoDB Table | `connect-queue-metadata-{env}-{deployId}` | Stores queue metadata |
| Provisioning Lambda | `connect-queue-provisioning-{env}-{deployId}` | Creates queues & quick connects from CSV |
| Query Lambda | `connect-queue-query-{env}-{deployId}` | Queries metadata (used by contact flows) |
| S3 Bucket | `connect-queue-provisioning-{account}-{env}-{deployId}` | CSV upload + results |
| Queue Transfer Flow | `Queue Transfer Flow - {env}-{deployId}` | Transfers calls based on TransferType |
| IAM Roles | Auto-generated | Least-privilege permissions |

## Prerequisites

1. **Amazon Connect Instance** - Note the Instance ID and ARN
2. **Hours of Operation** - Create one in Connect and note the ID
3. **AWS CLI** - Configured with appropriate permissions

### Finding Your IDs

```bash
# Instance ID & ARN
aws connect list-instances \
  --query 'InstanceSummaryList[*].[Id,Arn,InstanceAlias]' --output table

# Hours of Operation ID
aws connect list-hours-of-operations \
  --instance-id YOUR_INSTANCE_ID \
  --query 'HoursOfOperationSummaryList[*].[Id,Name]' --output table
```

## Deployment

```bash
aws cloudformation deploy \
  --template-file amazon-connect-provisioning.yaml \
  --stack-name QuickConnect-Queue-to-PhoneNumber-Matching-dev \
  --parameter-overrides \
    ConnectInstanceId=YOUR_INSTANCE_ID \
    ConnectInstanceArn=YOUR_INSTANCE_ARN \
    HoursOfOperationId=YOUR_HOURS_ID \
    Environment=dev \
    DeployId=v1 \
  --capabilities CAPABILITY_IAM
```

### Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `ConnectInstanceId` | Connect instance UUID | - |
| `ConnectInstanceArn` | Connect instance full ARN | - |
| `HoursOfOperationId` | Hours of operation UUID | - |
| `Environment` | `dev`, `staging`, or `prod` | `dev` |
| `DeployId` | Unique deployment identifier (max 10 chars) | `v1` |
| `LogRetentionDays` | CloudWatch log retention | `30` |

## CSV File Format

Upload CSV files to the `uploads/` folder in the S3 bucket:

```bash
aws s3 cp queues.csv s3://connect-queue-provisioning-{account}-{env}-{deployId}/uploads/
```

### Columns

| Column | Required | Description |
|--------|----------|-------------|
| `QueueName` | Yes | Name for the queue and quick connect |
| `TransferDestination` | Yes | Phone number OR existing queue name |
| `AssociateToQueue` | No | Queue to associate the quick connect with |
| `CallerID` | No | Caller ID phone number (normalized to E.164) |
| `TransferFlowArn` | No | Custom transfer flow ARN (QUEUE type only, overrides default) |

### Transfer Types

The system automatically detects whether `TransferDestination` is a phone number or a queue name:

| TransferDestination | Detected As | Behavior |
|---------------------|-------------|----------|
| `+1 (555) 123-4567` | `PHONE` | Quick connect transfers call to external phone number |
| `BasicQueue` | `QUEUE` | Quick connect transfers call to the specified queue |

### Sample CSV

```csv
QueueName,TransferDestination,AssociateToQueue,CallerID,TransferFlowArn
Sales,+1 (555) 123-4567,BasicQueue,+1 (555) 000-0001,
Support,(555) 987-6543,BasicQueue,+1 (555) 000-0002,
Billing,1-800-555-0100,BasicQueue,+1 (555) 000-0003,
Technical Support,ExistingHelpDesk,BasicQueue,+1 (555) 000-0004,
Customer Service,MainSupportQueue,BasicQueue,+44 20 7946 0958,arn:aws:connect:us-east-1:123456789012:instance/abc/contact-flow/def-456
Returns,555.321.9876,BasicQueue,+1 (555) 000-0006,
```

### Phone Number Normalization

| Input | Output (E.164) |
|-------|----------------|
| `+1 (555) 123-4567` | `+15551234567` |
| `(555) 987-6543` | `+15559876543` |
| `1-800-555-0100` | `+18005550100` |
| `5551234567` | `+15551234567` |
| `+44 20 7946 0958` | `+442079460958` |
| `555.321.9876` | `+15553219876` |

## Processing Flow

```
Upload CSV to s3://bucket/uploads/
        │
        ▼
Parse all rows, detect TransferType (PHONE or QUEUE)
        │
        ▼
┌─── Batch 1 (rows 1-10) ──────────────────────────────┐
│  Create 10 queues (0.5s delay between each)           │
│  Wait 5s for propagation                              │
│  Create 10 quick connects + associate (0.5s delay)    │
│  Save to DynamoDB                                     │
└───────────────────────────────────────────────────────┘
        │ 3s batch delay
        ▼
┌─── Batch 2 (rows 11-20) ─────────────────────────────┐
│  ... same process ...                                 │
└───────────────────────────────────────────────────────┘
        │
        ▼
Write results to S3:
  results/{timestamp}_{csv-name}/success.csv
  results/{timestamp}_{csv-name}/failure.csv
```

## Result Files

After each run, two CSV files are written to the S3 bucket:

### success.csv

Contains all successfully provisioned rows with their DynamoDB attributes:

```
QueueName,TransferType,TransferDestination,QueueArn,QuickConnectName,
PhoneNumberE164,TargetQueueArn,CallerIdE164,AssociatedQueueName,
TransferFlowArn,CreatedAt
```

### failure.csv

Contains rows that failed with the original CSV data plus error details:

```
QueueName,TransferDestination,AssociateToQueue,CallerID,TransferFlowArn,Error
TestFailQueue,NonExistentQueue,BasicQueue,+1 (555) 000-0003,,Destination queue not found: NonExistentQueue
```

## DynamoDB Table Schema

| Attribute | Type | Description |
|-----------|------|-------------|
| `QueueName` | String | Partition key |
| `QueueArn` | String | Full queue ARN |
| `TransferType` | String | `PHONE` or `QUEUE` |
| `TransferDestination` | String | Original value from CSV |
| `QuickConnectName` | String | Quick connect name (same as QueueName) |
| `QuickConnectId` | String | Quick connect ID |
| `PhoneNumberE164` | String | E.164 phone number (PHONE type only) |
| `TargetQueueArn` | String | Destination queue ARN (QUEUE type only) |
| `CallerIdE164` | String | Caller ID in E.164 format |
| `AssociatedQueueName` | String | Queue the quick connect is associated with |
| `TransferFlowArn` | String | Custom flow ARN (if specified) |
| `CreatedAt` | String | ISO 8601 timestamp |

## Query Lambda

The Query Lambda is used by contact flows and can be invoked directly. It returns **all** DynamoDB attributes for each record.

### Operations

#### Get Queue by Name

```json
{"operation": "get_queue", "QueueName": "Sales"}
```

Response includes all attributes from the table plus `"Found": "true"`.

#### List All Queues

```json
{"operation": "list_queues", "limit": 50}
```

#### Search by Phone Number

```json
{"operation": "search_by_phone", "PhoneNumber": "+15551234567"}
```

### Contact Flow Integration

The deployed Queue Transfer Flow automatically:

1. Invokes the Query Lambda with the queue name
2. Checks if a record was found
3. Checks `TransferType`:
   - **PHONE** - Transfers the call to the external phone number (`PhoneNumberE164`)
   - **QUEUE** - Ends the flow, allowing the quick connect's queue config to transfer to the target queue

## Utilities

### update_dynamodb_pk.py

Standalone script to update DynamoDB partition keys from a CSV file. Useful for migrating or correcting `PhoneNumberE164` values. See `sample-queue-phones.csv` for the expected format.

## Cleanup

1. Empty the S3 bucket:
```bash
aws s3 rm s3://connect-queue-provisioning-{account}-{env}-{deployId} --recursive
```

2. Delete quick connects and queues created by the system (CloudFormation cannot delete these automatically):
```bash
# List and delete quick connects
aws connect list-quick-connects --instance-id YOUR_INSTANCE_ID --quick-connect-types QUEUE

# List and delete queues
aws connect list-queues --instance-id YOUR_INSTANCE_ID --queue-types STANDARD
```

3. Delete the stack:
```bash
aws cloudformation delete-stack --stack-name QuickConnect-Queue-to-PhoneNumber-Matching-dev
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `ResourceNotFoundException` on ListQueues | Verify `ConnectInstanceId` is correct and matches the deployment region |
| `InvalidParameterException` on ListQueues | Use the instance UUID only, not the full ARN |
| `ServiceLimitExceeded` on contact flow | Delete unused contact flows in the Connect instance |
| `CREATE_FAILED` on IAM role | Role name already exists from a previous deployment; stack uses auto-generated names to avoid this |
| Lambda timeout | Lambda is set to 15 minutes (max); reduce batch size or increase delays if hitting API throttling |
| Quick connects not visible to agents | Ensure `AssociateToQueue` matches an existing queue name exactly |

### Viewing Logs

```bash
aws logs tail /aws/lambda/connect-queue-provisioning-{env}-{deployId} --follow
```
