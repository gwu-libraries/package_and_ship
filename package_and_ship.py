import os
import time
import logging
from pathlib import Path
import bagit
from asnake.aspace import ASpace
from asnake.utils import find_closest_value
from dateutil import parser
from dateutil.relativedelta import relativedelta
from user.config import config
from datetime import datetime
import boto3
import base64
import binascii
from boto3.s3.transfer import TransferConfig
import argparse

# Set up logging
current_time = datetime.now().strftime("%Y-%m-%d_%H-%M")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(f"logs/package_ship_{current_time}.log", mode='a'),  
        logging.StreamHandler() 
    ]
)

# log counters global variables
successful_uploads = 0
failed_uploads = 0
successful_bags = 0
failed_bags = 0

# Initialize ArchivesSpace client
def init_aspace_client():
    """Initializes and returns an ArchivesSpace client."""
    try:
        # Create ASpace client
        aspace_client = ASpace(
            baseurl=config.get('aspace_host'),
            username=config.get('aspace_user'),
            password=config.get('aspace_pass')
        ).client
        
        # Test the connection to ensure it's successful
        if aspace_client:
            logging.info("Connected to ArchivesSpace successfully.")
        else:
            logging.error("Failed to connect to ArchivesSpace.")
            raise ConnectionError("Failed to connect to ArchivesSpace.")
        
        return aspace_client

    except Exception as e:
        logging.error(f"Error initializing ArchivesSpace client: {str(e)}")
        raise

# Initialize the aspace client
as_client = init_aspace_client()

# Initialize AWS S3 client
s3_client = boto3.client(
    's3',
    aws_access_key_id=config['aws_access'],
    aws_secret_access_key=config['aws_secret'],
    region_name=config['aws_region']
)

class ASpaceDateFormatter:
    def get_date_range(self, dates_array):
        """Gets maximum and minimum dates from an AS date array."""
        start_dates = []
        end_dates = []

        for date in dates_array:
            # Ensure that 'begin' and 'end' are present and valid
            begin_date = date.get('begin')
            end_date = date.get('end')

            # Skip if 'begin' is missing or invalid
            if not begin_date or begin_date.lower() == 'undated':
                continue  # Skip this entry if there's no valid start date

            # Add 'begin' date to start_dates list
            start_dates.append(begin_date)

            # If it's a 'single' date type, use 'begin' for both start and end
            if date['date_type'] == 'single':
                end_dates.append(begin_date)
            elif end_date:  # Otherwise, use 'end' if it's provided
                end_dates.append(end_date)
            else:  # If no 'end' is provided, assume 'begin' as 'end'
                end_dates.append(begin_date)

        # Check if there are valid start and end dates
        if not start_dates or not end_dates:
            logging.warning("No valid start or end dates found.")
            return None, None

        # Return sorted start and end dates (earliest start and latest end)
        return sorted(start_dates)[0], sorted(end_dates)[-1]

    def format_aspace_date(self, start_date, end_date):
        """Formats ASpace dates so that they can be parsed."""
        parsed_start = parser.isoparse(start_date)
        parsed_end = parser.isoparse(end_date)
        formatted_start = parsed_start.strftime('%Y-%m-%d')

        if len(end_date) == 4:  # If end date is only year, assume end of year
            formatted_end = (
                parsed_end + relativedelta(month=12, day=31)).strftime('%Y-%m-%d')
        elif len(end_date) == 7:  # If end date is a month, assume end of month
            formatted_end = (
                parsed_end + relativedelta(day=31)).strftime('%Y-%m-%d')
        else:  # If it’s a full date, keep it as is
            formatted_end = end_date

        return formatted_start, formatted_end

    def process_dates(self, dates_array):
        """Fetch the date range and format the dates."""
        start_date, end_date = self.get_date_range(dates_array)
        
        # If no valid date range is found, log and return default values
        if not start_date or not end_date:
            logging.warning("No valid date range found. Using default date range: 1900-01-01 to 9999-12-31.")
            return '1900-01-01', '9999-12-31'
        
        # Format the dates if valid range exists
        formatted_start_date, formatted_end_date = self.format_aspace_date(start_date, end_date)
        
        return formatted_start_date, formatted_end_date
    
class aspaceOperations:
    def uri_from_refid(self, refid, max_retries=3, retry_delay=10):
        """
        Fetch the URI of an archival object from ArchivesSpace by its ref_id.
        Includes retry logic for transient network errors.
        """
        
        find_by_refid_url = f"repositories/{config['aspace_repo']}/find_by_id/archival_objects?ref_id[]={refid}"
        last_exception = None

        for attempt in range(1, max_retries + 1):
            try:
                response = as_client.get(find_by_refid_url)
                response.raise_for_status()
                results = response.json()
                
                if len(results.get("archival_objects")) == 1:
                    return results['archival_objects'][0]['ref']
                else:
                    # Logic error (0 or >1 results), do not retry this specific error
                    raise Exception(f"{len(results.get('archival_objects'))} results found for search {find_by_refid_url}. Expected one result.")
            
            except Exception as e:
                last_exception = e
                if attempt < max_retries:
                    logging.warning(f"Attempt {attempt}/{max_retries} failed for refid {refid}: {str(e)}. Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    logging.error(f"All {max_retries} attempts failed for refid {refid}.")

        if last_exception:
            logging.error(f"Error fetching URI for refid {refid}: {str(last_exception)}")
            raise last_exception
        
    def get_ao_title(self, obj_uri):
        """"fetch the title (not the complete display string) of an AO via its refid"""
        obj_metadata = as_client.get(obj_uri).json()
        object_title = obj_metadata.get('title')
        return object_title

    def get_collection_id(self, obj_uri):
        """Fetches the collection_id from an archival object URI in ArchivesSpace."""
        try:
            # Fetch the metadata for the archival object using the URI
            obj_metadata = as_client.get(obj_uri).json()
            # Extract the collection_id from the resource metadata (if it exists)
            collection_resource = obj_metadata.get('resource', {})
            collection_uri = collection_resource.get('ref', '')
            collection_id = ''
            if collection_uri:
                collection_json = as_client.get(collection_uri).json()
                collection_id = collection_json.get('id_0', '').lower()
            return collection_id

        except Exception as e:
            logging.error(f"Error fetching collection_id for URI {obj_uri}: {str(e)}")
            raise

    def create_preservation_dao(self, file_uri, refid):
        # Fetch the archival object URI
        obj_uri = self.uri_from_refid(refid)
        ao_record = as_client.get(obj_uri).json()
        
        # Check for existing DAO records
        existing_digital_object_IDs = [
            as_client.get(instance['digital_object']['ref']).json()['digital_object_id']
            for instance in ao_record["instances"]
            if instance["instance_type"] == "digital_object"
        ]
        
        # Generate a new digital object ID
        new_digital_object_id = refid

        # Ensure the digital object ID is unique
        while new_digital_object_id in existing_digital_object_IDs:
            # Try to make the new_do_id unique by appending _presCopy.
            new_digital_object_id = f"{new_digital_object_id}_presCopy"
            logging.warning(f"Digital object ID {new_digital_object_id} already exists. Attempting new ID.")

        file_publish = False 
        file_version = {'file_uri': file_uri, 'publish': file_publish, "use_statement" : "access_staff"}

        dao_data = {
            "jsonmodel_type": "digital_object",
            "publish": True, 
            "title": f"{ao_record['display_string']}",
            "digital_object_id": new_digital_object_id,
            "file_versions": [file_version],
        }

        # Post the new DAO record
        try:
            dao_response = as_client.post(f"repositories/{config['aspace_repo']}/digital_objects", json=dao_data).json()
            dao_ref = dao_response["uri"]
            logging.info(f"Created new DAO: {dao_ref}")
        except Exception as e:
            logging.error(f"Error creating DAO: {str(e)}")
            return
        # Link the new DAO to the archival object
        try:
            instances = ao_record.get("instances", [])
            instances.append({"instance_type": "digital_object", "digital_object": {"ref": dao_ref}})
            ao_record["instances"] = instances
            as_client.post(obj_uri, json=ao_record)
            logging.info(f"Linked new DAO {dao_ref} to AO {obj_uri}")
        except Exception as e:
            logging.error(f"Error updating AO with new instance: {str(e)}")
            return
        return dao_data

class S3handler:
    @staticmethod
    def hex_to_base64(hex_string):
        """Converts a hex string to a Base64 string."""
        return base64.b64encode(binascii.unhexlify(hex_string)).decode('utf-8')

    @staticmethod
    def load_bag_checksums(bag_dir):
        """
        Parses manifest-sha256.txt.
        Returns: dict { 'relative/path': 'ORIGINAL_HEX_STRING' }
        """
        checksums = {}
        manifests = ['manifest-sha256.txt', 'tagmanifest-sha256.txt']
        
        for manifest_name in manifests:
            manifest_path = os.path.join(bag_dir, manifest_name)
            if os.path.exists(manifest_path):
                with open(manifest_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        # BagIt lines are: CHECKSUM  FILENAME
                        # split maxsplit=1 to handle filenames with spaces correctly
                        parts = line.strip().split(maxsplit=1)
                        if len(parts) == 2:
                            hex_hash, rel_path = parts
                            clean_path = rel_path.strip().replace('\\', '/')
                            # Store the raw hash (do not convert yet)
                            checksums[clean_path] = hex_hash
        return checksums

    @staticmethod
    def transfer_to_s3(bag_dir: Path, s3_key: str):
        global successful_uploads, failed_uploads
        aws_bucket = config['aws_bucket']
        
        # Configure Multipart settings
        transfer_config = TransferConfig(
            multipart_threshold=100 * 1024 * 1024, 
            max_concurrency=10,
            multipart_chunksize=25 * 1024 * 1024,
            use_threads=True
        )

        logging.info(f"Starting S3 transfer for directory: {bag_dir} to {aws_bucket}/{s3_key}")

        if config['dry_run']:
            logging.info(f"Dry run: Would transfer files from {bag_dir} to S3 path {s3_key}")
            return

        try:
            # Checksums are now loaded as HEX strings
            bag_checksums = S3handler.load_bag_checksums(bag_dir)
            upload_failed = False
            
            for root, _, files in os.walk(bag_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    # Clean s3 path and relative path for lookup
                    rel_path = os.path.relpath(file_path, bag_dir).replace("\\", "/")
                    s3_path = os.path.join(s3_key, rel_path).replace("\\", "/")
                    
                    try:
                        # Check if the file already exists in S3
                        try:
                            s3_client.head_object(Bucket=aws_bucket, Key=s3_path)
                            logging.warning(f"File {s3_path} already exists. Skipping.")
                            continue
                        except s3_client.exceptions.ClientError as e:
                            if e.response['Error']['Code'] != '404':
                                raise

                        file_size = os.path.getsize(file_path)
                        extra_args = {'ChecksumAlgorithm': 'SHA256'}
                        
                        if rel_path in bag_checksums:
                            # 1. Get the Hex (User Friendly)
                            hex_hash = bag_checksums[rel_path]
                            
                            # 2. Convert to Base64 (S3 System Friendly)
                            base64_hash = S3handler.hex_to_base64(hex_hash)

                            # 3. Store HEX in Metadata (Matches manifest.txt visually)
                            extra_args['Metadata'] = {
                                'bagit-sha256': hex_hash 
                            }

                            if file_size < transfer_config.multipart_threshold:
                                # 4. Pass Base64 to S3 for enforcement
                                extra_args['ChecksumSHA256'] = base64_hash
                                logging.info(f"Uploading {rel_path} (Strict Checksum + Metadata).")
                            else:
                                logging.info(f"Uploading {rel_path} (Multipart - Metadata added).")
                        else:
                            logging.info(f"Uploading {rel_path} (Calculated on fly).")

                        s3_client.upload_file(
                            file_path, 
                            aws_bucket, 
                            s3_path, 
                            Config=transfer_config,
                            ExtraArgs=extra_args
                        )
                            
                    except s3_client.exceptions.ClientError as e:
                        if "BadDigest" in str(e) or "ChecksumMismatch" in str(e):
                             logging.error(f"CRITICAL: Integrity failure for {file_path}.")
                        else:
                             logging.error(f"AWS Error uploading {file_path}: {e}")
                        upload_failed = True
                    except Exception as e:
                        logging.exception(f"Unexpected error uploading {file_path}")
                        upload_failed = True
                        raise

            if upload_failed:
                logging.error(f"Failed to transfer some files in {bag_dir}")
                failed_uploads += 1
            else:
                logging.info(f"Successfully transferred {bag_dir}")
                successful_uploads += 1

        except Exception as e:
            logging.exception(f"Error transferring directory {bag_dir}: {e}")
            failed_uploads += 1

    @staticmethod
    def s3_key_construction(aws_bucket, refid, collection_id):
        """Constructs the S3 key for the given bucket, refid, and collection_id."""
        base_s3_path = config.get('base_s3_path', '') 
        s3_key = os.path.join(base_s3_path, collection_id, refid).replace("\\", "/")
        return s3_key
    
    @staticmethod
    def construct_s3_cloudfront_URI(s3_path):
        """Takes an S3 prefix and returns a CloudFront URI."""
        folder_prefix = "inventory.html?folder="
        cloudfront_base_uri = config.get('cloudfront_base_URI')
        cloudfront_URI = cloudfront_base_uri + folder_prefix + s3_path
        return cloudfront_URI

def get_refids(input_directory):
    """Fetch the refids (folder names) from the given directory."""
    refids = []
    for folder in os.listdir(input_directory):
        folder_path = os.path.join(input_directory, folder)
        if os.path.isdir(folder_path):
            refids.append(folder)
    return refids

def create_bag_and_upload(bag_dir: Path, rights_ids: list, dry_run=False):
    """Creates a BagIt bag from a directory and its metadata, with dry run functionality."""
    global successful_bags, failed_bags
    try:
        # Check if the directory exists and has files
        if not bag_dir.exists() or not any(bag_dir.iterdir()):
            logging.error(f"The directory {bag_dir} is empty or doesn't exist.")
            failed_bags += 1
            return
        
        # Fetch the URI from ArchivesSpace based on refid (folder name)
        refid = bag_dir.name  
        obj_uri = aspace_ops.uri_from_refid(refid)

        # Fetch the dates closest to the record (move up the archival description tree until it finds a record with a date).
        dates_array = find_closest_value(obj_uri, 'dates', as_client)

        # Process the dates and metadata
        aspace_date_formatter = ASpaceDateFormatter()
        formatted_start_date, formatted_end_date = aspace_date_formatter.process_dates(dates_array)

        # Fetch the collection_id
        collection_id = aspace_ops.get_collection_id(obj_uri)

        # Fetch the AO title
        object_title = aspace_ops.get_ao_title(obj_uri)

        # Default to empty rights IDs if none are provided
        if not rights_ids:
            rights_ids = ['']

        metadata = {
            'ArchivesSpace-URI': obj_uri,
            'Start-Date': formatted_start_date,
            'Title': object_title,
            'End-Date': formatted_end_date,
            'Origin': 'digitization',
            'Rights-ID': '',
            'Collection-ID': collection_id,
            'BagIt-Profile-Identifier': 'scrc-digitization-profile.json'
        }

        # Skip bag creation if it's a dry run
        if dry_run:
            logging.info(f"Dry run: Skipping Bag creation for {bag_dir}. Metadata: {metadata}")
        else:
            # Create the Bag
            bagit.make_bag(str(bag_dir), metadata, checksum=['sha256'])
            logging.info(f'Bag created from {bag_dir}.')
            successful_bags += 1

        # Construct the S3 key
        s3_key = S3handler.s3_key_construction(config['aws_bucket'], refid, collection_id)

        # If dry_run is True, log the action but don't upload
        if dry_run:
            logging.info(f"Dry run: Skipping S3 upload for {bag_dir}. S3 Key would be: {s3_key}")
        else:
            # Transfer the bag to S3
            S3handler.transfer_to_s3(bag_dir, s3_key)

        return s3_key

    except Exception as e:
        logging.exception(f"Error creating bag for {bag_dir}: {str(e)}")
        failed_bags += 1

if __name__ == "__main__":
    # 1. Set argument parser 
    cli_parser = argparse.ArgumentParser(
        description="Packager and Shipper pipeline for ArchivesSpace and S3."
    )
    cli_parser.add_argument(
        '-r', '--refid', 
        type=str, 
        help="Specify a single ref_id folder to process. If omitted, the entire directory will be processed."
    )
    args = cli_parser.parse_args()

    # 2. Load config values
    input_directory = config['input_directory']
    rights_ids = config.get('rights_ids', [])
    dry_run = config.get('dry_run', False)

    # 3. Create an instance of the aspaceOperations class
    aspace_ops = aspaceOperations()

    # 4. Determine execution mode (Single vs. Batch)
    if args.refid:
        # Verify the requested folder actually exists
        target_path = Path(input_directory) / args.refid
        if target_path.exists() and target_path.is_dir():
            logging.info(f"Targeted execution: Processing single ref_id '{args.refid}'")
            refids = [args.refid]
        else:
            logging.error(f"The directory for ref_id '{args.refid}' does not exist at {target_path}")
            refids = []
    else:
        # Fall back to default batch mode
        logging.info("Batch execution: Processing all directories in input_directory.")
        refids = get_refids(input_directory)

    # 5. Process the selected ref_id(s)
    for refid in refids:
        try:
            logging.info(f"starting {refid}")
            bag_dir = Path(input_directory) / refid
            s3_key = create_bag_and_upload(bag_dir, rights_ids, dry_run)
            
            if s3_key is None:
                continue

            # Skip DAO creation if it's a dry run
            if not dry_run:
                # create the file_uri depending on what bucket the content is going to
                if 'scrc-digcol' in config.get('aws_bucket'):
                    file_uri = S3handler.construct_s3_cloudfront_URI(s3_key)
                elif 'scrc-preservation' in config.get('aws_bucket'):
                    file_uri = s3_key
                aspace_ops.create_preservation_dao(file_uri, refid)
            else:
                logging.info(f"Dry run: Skipping DAO creation for {refid}.")
        
        except Exception as e:
            logging.error(f"Error processing {refid}: {e}")

    # Output summaries
    logging.info(f"Summary: {successful_uploads} successful uploads, {failed_uploads} failed uploads.")
    logging.info(f"Summary: {successful_bags} successful bags, {failed_bags} failed bags.")