#ArchivesSpace
aspace_user = '' #put aspace username here
aspace_pass = '' #put aspace password here
aspace_repo = '2' #scoped narrowly to the SCRC repository
aspace_host = '' #put API host URL here. Make sure you don't have a slash ('/') at the end of the URL.
aspace_pui = 'https://searcharchives.library.gwu.edu/repositories/2/' #put the public user interface URL here. It should include the specific repository and end with a / (ex. test.edu/repositories/2/)

#AWS/CloudFront 
aws_access_key = ''
aws_secret_key = ''
aws_bucket = ''
base_s3_path = ''
aws_region = ''
cloudfront_base_URI = ''
preservation_aws_bucket= ''

#input directory
input_directory = ''

dry_run = True

config = {
    'aspace_user': aspace_user,
    'aspace_pass': aspace_pass,
    'aspace_repo': aspace_repo,
    'aspace_host': aspace_host,
    'aspace_pui': aspace_pui,
    'aws_access': aws_access_key,
    'aws_secret': aws_secret_key,
    'base_s3_path': base_s3_path,
    'aws_bucket': aws_bucket,
    'preservation_aws_bucket': preservation_aws_bucket,
    'aws_region': aws_region,
    'cloudfront_base_URI': cloudfront_base_URI,
    'input_directory':input_directory,
    'dry_run': dry_run
}