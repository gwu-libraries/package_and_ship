# ArchivesSpace
aspace_user = ''  # ArchivesSpace username
aspace_pass = ''  # ArchivesSpace password
aspace_repo = '2' # ArchivesSpace repository ID
aspace_host = ''  # API host URL (no trailing slash)

# AWS / CloudFront 
aws_access = ''   # AWS Access Key ID
aws_secret = ''   # AWS Secret Access Key
aws_bucket = ''   # S3 Bucket Name
aws_region = ''   # AWS Region (e.g., 'us-east-1')
base_s3_path = ''
cloudfront_base_URI = ''

# Input & Execution Settings
input_directory = ''
dry_run = False

config = {
    'aspace_user': aspace_user,
    'aspace_pass': aspace_pass,
    'aspace_repo': aspace_repo,
    'aspace_host': aspace_host,
    'aws_access': aws_access,
    'aws_secret': aws_secret,
    'aws_bucket': aws_bucket,
    'aws_region': aws_region,
    'base_s3_path': base_s3_path,
    'cloudfront_base_URI': cloudfront_base_URI,
    'input_directory': input_directory,
    'dry_run': dry_run
}