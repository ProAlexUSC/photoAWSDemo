# CPU-only override for aws workspace (USE_GPU=0)
# 用法：tofu apply -var-file=aws.tfvars -var-file=aws-cpu.tfvars
# 后者覆盖前者
#
# m7i-flex.large 是 free-tier eligible 中 RAM 最大的（8GB 给 buffalo_l + Python 留余量），
# 2 vCPU 是 free-tier 上限；vcpus=2 因此对应。
# 升级到 Paid Plan 后可换回 c5.xlarge / c5.2xlarge。
batch_use_gpu        = false
batch_instance_types = ["m7i-flex.large"]
worker_vcpus         = 2
worker_memory        = 4096
