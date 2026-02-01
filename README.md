# Amazon Connect Queue Provisioning System

Automated provisioning of Amazon Connect queues and Quick Connects from CSV files using AWS CloudFormation.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              AWS Cloud                                       │
│                                                                             │
│  ┌──────────┐    ┌─────────────────────┐    ┌──────────────────────┐       │
│  │  User    │    │      S3 Bucket      │    │  Provisioning Lambda │       │
│  │ uploads  │───▶│  /uploads/*.csv     │───▶│                      │       │
│  │  CSV     │    │                     │    │  1. Create Queues    │       │
│  └──────────┘    └─────────────────────┘    │  2. Wait 5 seconds   │       │
│                                             │  3. Create QCs       │       │
│                                             │  4. Associate QCs    │       │
│                                             └──────────┬───────────┘       │
│                                                        │                    │
│                         ┌──────────────────────────────┼───────────┐       │
│                         │                              │           │       │
│                         ▼                              ▼           ▼       │
│                  ┌─────────────┐              ┌─────────────┐ ┌─────────┐  │
│                  │  DynamoDB   │              │   Amazon    │ │ Amazon  │  │
│                  │   Table     │              │  Connect    │ │ Connect │  │
│                  │             │              │  Queues     │ │  Quick  │  │
│                  │ - QueueName │              │             │ │Connects │  │
│                  │ - QueueId   │              └─────────────┘ └─────────┘  │
│                  │ - PhoneE164 │                                           │
│                  │ - AssocQueue│                                           │
│                  └──────┬──────┘                                           │
│                         │                                                  │
│                         ▼                                                  │
│                  ┌─────────────┐         ┌─────────────────────┐          │
│                  │   Query     │◀────────│   Amazon Connect    │          │
│                  │   Lambda    │         │   Contact Flows     │          │
│                  │             │         │                     │          │
│                  └─────────────┘         └─────────────────────┘          │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
```

## What Gets Created

The CloudFormation template creates all of these resources:

| Resource | Name | Purpose |
|----------|------|---------|
| S3 Bucket | `connect-queue-provisioning-{account}-{env}` | CSV upload destination |
| DynamoDB Table | `connect-queue-metadata-{env}` | Stores queue metadata |
| Provisioning Lambda | `connect-queue-provisioning-{env}` | Creates queues & quick connects |
| Query Lambda | `connect-queue-query-{env}` | Queries metadata from contact flows |
| IAM Roles | Various | Permissions for Lambda functions |
| CloudWatch Log Groups | `/aws/lambda/...` | Error logging only |

## Prerequisites

Before deploying, you need:

1. **Amazon Connect Instance** - Note the Instance ID and ARN
2. **Hours of Operation** - Create one in Connect and note the ID
3. **Contact Flow** - Create a transfer flow for Quick Connects and note the ID
4. **AWS CLI** - Configured with appropriate permissions

### Finding Your IDs

**Instance ID & ARN:**
```bash
aws connect list-instances --query 'InstanceSummaryList[*].[Id,Arn,InstanceAlias]' --output table
```

**Hours of Operation ID:**
```bash
aws connect list-hours-of-operations --instance-id YOUR_INSTANCE_ID --query 'HoursOfOperationSummaryList[*].[Id,Name]' --output table
```

**Contact Flow ID:**
```bash
aws connect list-contact-flows --instance-id YOUR_INSTANCE_ID --query 'ContactFlowSummaryList[?Type==`QUEUE_TRANSFER`].[Id,Name]' --output table
```

## Deployment

### Step 1: Deploy the CloudFormation Stack

```bash
aws cloudformation deploy \
  --template-file amazon-connect-provisioning.yaml \
  --stack-name connect-queue-provisioning \
  --parameter-overrides \
    ConnectInstanceId=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee \
    ConnectInstanceArn=arn:aws:connect:us-east-1:123456789012:instance/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee \
    HoursOfOperationId=11111111-2222-3333-4444-555555555555 \
    TransferContactFlowId=66666666-7777-8888-9999-000000000000 \
    Environment=dev \
  --capabilities CAPABILITY_NAMED_IAM
```

### Step 2: Get the S3 Bucket Name

```bash
aws cloudformation describe-stacks \
  --stack-name connect-queue-provisioning \
  --query 'Stacks[0].Outputs[?OutputKey==`S3BucketName`].OutputValue' \
  --output text
```

### Step 3: Upload Your CSV File

```bash
aws s3 cp queues.csv s3://connect-queue-provisioning-123456789012-dev/uploads/
```

**Important:** Files must be uploaded to the `uploads/` folder and have `.csv` extension.

## Processing Flow

When you upload a CSV, the following happens automatically:

```
1. S3 detects new .csv file in uploads/
           │
           ▼
2. Triggers Provisioning Lambda
           │
           ▼
3. Lambda loads existing queues from Connect
           │
           ▼
4. PHASE 1: Creates ALL queues from CSV
           │
           ▼
5. Waits 5 seconds (propagation delay)
           │
           ▼
6. Reloads queue mapping
           │
           ▼
7. PHASE 2: Creates Quick Connects (same name as queue)
           │
           ▼
8. Associates Quick Connects to target queues
           │
           ▼
9. Stores metadata in DynamoDB
```

## CSV File Format

The CSV file must contain the following columns:

| Column | Required | Description |
|--------|----------|-------------|
| `QueueName` | Yes | Name for the Amazon Connect queue (also used as Quick Connect name) |
| `PhoneNumber` | Yes | Phone number (any format, converted to E.164) |
| `AssociateToQueue` | Yes | Name of the queue to associate the Quick Connect with |

### How It Works

For each row, the system:
1. Creates a **Queue** with the name from `QueueName`
2. Creates a **Quick Connect** with the **same name** as the queue
3. Associates the Quick Connect with the queue specified in `AssociateToQueue`

This allows agents in one queue to see Quick Connects for transferring to other queues.

### Supported Phone Number Formats

The system automatically converts phone numbers to E.164 format:

| Input Format | Output (E.164) |
|--------------|----------------|
| `+1 (555) 123-4567` | `+15551234567` |
| `(555) 987-6543` | `+15559876543` |
| `1-800-555-0100` | `+18005550100` |
| `5551234567` | `+15551234567` |
| `+44 20 7946 0958` | `+442079460958` |
| `555.321.9876` | `+15553219876` |

### Sample CSV

```csv
QueueName,PhoneNumber,AssociateToQueue
Sales,(555) 123-4567,Main
Support,1-800-555-0199,Main
Billing,(555) 444-5555,Support
```

In this example:
- `Sales` queue is created, Quick Connect `Sales` is added to `Main` queue
- `Support` queue is created, Quick Connect `Support` is added to `Main` queue  
- `Billing` queue is created, Quick Connect `Billing` is added to `Support` queue

Agents in the `Main` queue will see `Sales` and `Support` as transfer options.

## Using the Query Lambda

The Query Lambda can be invoked from Amazon Connect contact flows or directly.

### Operations

#### 1. Get Queue by Name

```json
{
  "operation": "get_queue",
  "queue_name": "Sales"
}
```

**Response:**
```json
{
  "QueueName": "Sales",
  "QueueId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
  "QuickConnectName": "Sales",
  "QuickConnectId": "11111111-2222-3333-4444-555555555555",
  "PhoneNumber": "+15551234567",
  "AssociatedQueueName": "Main",
  "Found": "true"
}
```

All values are at the top level for easy access in Connect contact flows via `$.External.QueueName`, `$.External.PhoneNumber`, etc.

#### 2. List All Queues

```json
{
  "operation": "list_queues",
  "limit": 50
}
```

#### 3. Search by Phone Number

```json
{
  "operation": "search_by_phone",
  "phone_number": "+15551234567"
}
```

### Amazon Connect Contact Flow Integration

In your contact flow, use the **Invoke AWS Lambda function** block:

1. Select the `connect-queue-query-{environment}` function
2. Add function input parameters:
   - `Operation`: `get_queue`
   - `QueueName`: Use a contact attribute or static value

3. Access returned attributes:
   - `$.External.QueueId`
   - `$.External.PhoneNumber`
   - `$.External.Found`

## DynamoDB Table Schema

| Attribute | Type | Description |
|-----------|------|-------------|
| `QueueName` | String | Partition key |
| `QueueId` | String | Amazon Connect queue ID |
| `QuickConnectName` | String | Quick Connect name (same as QueueName) |
| `QuickConnectId` | String | Quick Connect ID |
| `PhoneNumberE164` | String | Phone number in E.164 format |
| `AssociatedQueueName` | String | Name of queue the Quick Connect is associated with |
| `CreatedAt` | String | ISO 8601 timestamp |
| `UpdatedAt` | String | ISO 8601 timestamp |

**Note:** Only `AssociatedQueueName` is stored. The Queue ID is resolved at runtime by fetching all queues and mapping names to IDs.

## Monitoring

### CloudWatch Logs

- Provisioning Lambda: `/aws/lambda/connect-queue-provisioning-{env}`
- Query Lambda: `/aws/lambda/connect-queue-query-{env}`

### CloudWatch Metrics

Monitor the following metrics:
- `AWS/Lambda/Invocations`
- `AWS/Lambda/Errors`
- `AWS/Lambda/Duration`
- `AWS/DynamoDB/ConsumedReadCapacityUnits`
- `AWS/DynamoDB/ConsumedWriteCapacityUnits`

## Security

The template implements the following security measures:

- **S3 Bucket**: Encryption at rest, public access blocked, versioning enabled
- **DynamoDB**: Encryption at rest, point-in-time recovery enabled
- **IAM**: Least-privilege policies for Lambda functions
- **Lambda**: Functions have only required permissions

## Cleanup

To delete all resources, simply delete the stack:

```bash
aws cloudformation delete-stack --stack-name connect-queue-provisioning
```

**Note:** The S3 bucket is automatically emptied before deletion. No manual cleanup required.

The stack includes a cleanup Lambda that:
- Deletes all objects in the bucket
- Deletes all object versions (since versioning is enabled)
- Runs automatically when the stack is deleted

## Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| Lambda timeout | Increase `Timeout` in CloudFormation template |
| Permission denied | Verify IAM policies include required Connect permissions |
| Queue already exists | System handles duplicates gracefully, logs warning |
| Invalid phone number | Check CloudWatch logs for formatting warnings |

### Viewing Logs

```bash
aws logs tail /aws/lambda/connect-queue-provisioning-dev --follow
```

## License

MIT License - See LICENSE file for details.
