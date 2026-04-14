#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

export SFN_FILE="$1"

uv run python -c "
import boto3, os

with open(os.environ['SFN_FILE']) as f:
    definition = f.read()

sfn = boto3.client('stepfunctions')
region = os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
name = 'photo-pipeline'
arn = f'arn:aws:states:{region}:000000000000:stateMachine:{name}'

try:
    sfn.create_state_machine(
        name=name,
        definition=definition,
        roleArn='arn:aws:iam::000000000000:role/sfn-role',
    )
    print(f'Created state machine {name}')
except sfn.exceptions.StateMachineAlreadyExists:
    sfn.update_state_machine(stateMachineArn=arn, definition=definition)
    print(f'Updated state machine {name}')
"
