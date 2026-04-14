#!/usr/bin/env bash
# 用 uv run 调 Python 创建/更新 MiniStack Step Functions 状态机
set -euo pipefail

SFN_FILE="$1"
REGION="${AWS_DEFAULT_REGION:-us-east-1}"

cd "$(dirname "$0")/.."

uv run python -c "
import boto3, json, sys, os

with open('$SFN_FILE') as f:
    definition = f.read()

sfn = boto3.client('stepfunctions')
name = 'photo-pipeline'
arn = f'arn:aws:states:${REGION}:000000000000:stateMachine:{name}'

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
