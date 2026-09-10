conda create --name mono2esls python=3.11 -y
(conda run -n mono2esls python --version)
cd d:\Emma\MyLab\Mono2SLs\OpenHarness
conda run -n mono2esls pip install -e .

# 验证版本
conda run -n mono2esls oh --version
# 验证命令可用
conda run -n mono2esls oh --help
# 查看配置状态（不执行，仅预览）
conda run -n mono2esls oh --dry-run
conda run -n mono2esls oh --dry-run -p "测试配置"
# 初始化配置（需要输入 API 密钥）
conda run -n mono2esls oh setup
(oh provider edit qwen --base-url https://{WorkspaceId}.us-east-1.maas.aliyuncs.com/compatible-mode/v1)