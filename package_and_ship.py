import os
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
from datetime import datetime


# Set up logging
current_time=datetime.now().strftime("%Y-%m-%d_%H-%M")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(f"logs/package_ship_{current_time}.log", mode='a'),  
        logging.StreamHandler() 
    ]
)

#log counters global variables
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
    def __init__(self):
        pass

    def get_date_range(self, dates_array):
        """Gets maximum and minimum dates from an AS date array.

        Args:
            dates (list of dicts): ArchivesSpace date list

        Returns:
            start_date (str): earliest date in date list.
            end_date (str): latest date in date list
        """
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
        """Formats ASpace dates so that they can be parsed. 
        Assumes beginning of month or year if a start date, and end of month or
        year if an end date.

        Args:
            start_date (str): unformatted start date
            end_date (str): unformatted end date

        Returns:
            formatted_start_date (str): start date in format YYYY-MM-DD
            formatted_start_date (str): end date in format YYYY-MM-DD
        """
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
        """Fetch the date range and format the dates.

        Args:
            dates_array (list): list of dates

        Returns:
            formatted_start_date (str): formatted start date
            formatted_end_date (str): formatted end date
        """
        # Get the date range (start and end)
        start_date, end_date = self.get_date_range(dates_array)
        
        # If no valid date range is found, log and return default values
        if not start_date or not end_date:
            logging.warning("No valid date range found. Using default date range: 1900-01-01 to 9999-12-31.")
            return '1900-01-01', '9999-12-31'
        
        # Format the dates if valid range exists
        formatted_start_date, formatted_end_date = self.format_aspace_date(start_date, end_date)
        
        return formatted_start_date, formatted_end_date
    
class aspaceOperations:
    def uri_from_refid(self, refid):
        """Fetch the URI of an archival object from ArchivesSpace by its ref_id."""
        try:
            find_by_refid_url = f"repositories/{config['aspace_repo']}/find_by_id/archival_objects?ref_id[]={refid}"
            response = as_client.get(find_by_refid_url)
            response.raise_for_status()
            results = response.json()
            
            # Check if exactly one result is returned
            if len(results.get("archival_objects")) == 1:
                return results['archival_objects'][0]['ref']
            else:
                raise Exception(f"{len(results.get('archival_objects'))} results found for search {find_by_refid_url}. Expected one result.")
        
        except Exception as e:
            logging.error(f"Error fetching URI for refid {refid}: {str(e)}")
            raise

    def get_ao_title(self, obj_uri):
        """"fetch the title (not the complete display string) of an AO via its refid"""
        obj_metadata = as_client.get(obj_uri).json()
        #print(obj_metadata)
        object_title = obj_metadata.get('title')
        return object_title #AOs must have titles, so not sure if I need to catch errors

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
                collection_id = collection_json.get('id_0', '').lower()  # Convert to lowercase
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
            logging.info(f"Digital object ID {new_digital_object_id} already exists. Attempting new ID.")

        file_publish = False  # Do not publish CloudFront links
        file_version = {'file_uri': file_uri, 'publish': file_publish}

        dao_data = {
            "jsonmodel_type": "digital_object",
            "publish": True,  # Publish the DAO, but not the file_version
            "title": f"Preservation Copy: {ao_record['display_string']}",  # Using the title of the AO as the basis for the DAO title
            "digital_object_id": new_digital_object_id,  # Use the unique ID for the DAO
            "file_versions": [file_version]
        }

        # Post the new DAO record
        try:
            dao_response = as_client.post(f"repositories/{config['aspace_repo']}/digital_objects", json=dao_data).json()
            dao_ref = dao_response["uri"]
            logging.info(f"Created new DAO: {dao_ref}")
        except Exception as e:
            logging.error(f"Error creating DAO: {str(e)}")
            return

        # Link the new DAO record to the AO
        try:
            instances = ao_record.get("instances", [])  # Safely get instances
            instances.append({"instance_type": "digital_object", "digital_object": {"ref": dao_ref}})
            ao_record["instances"] = instances  # Update instances
            as_client.post(obj_uri, json=ao_record)  # Post the updated AO
            logging.info(f"Linked new DAO {dao_ref} to AO {obj_uri}")
        except Exception as e:
            logging.error(f"Error updating AO with new instance: {str(e)}")
            return
        return dao_data

class S3handler:
    def transfer_to_s3(bag_dir: Path, s3_key: str):
        """Transfers the created bag to the specified S3 location."""
        global successful_uploads, failed_uploads
        aws_bucket = config['aws_bucket']
        logging.info(f"Starting S3 transfer for directory: {bag_dir} to {aws_bucket}/{s3_key}")
        try:
            #flag to track upload
            upload_failed = False
            aws_bucket = config['aws_bucket']
            for root, _, files in os.walk(bag_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    #clean s3 path
                    s3_path = os.path.join(s3_key, os.path.relpath(file_path, bag_dir)).replace("\\", "/")
                    try:
                        # Check if the file already exists in S3 by checking for metadata via HeadObject
                        s3_client.head_object(Bucket=aws_bucket, Key=s3_path)
                        logging.warning(f"File {s3_path} already exists in bucket {aws_bucket}. Skipping upload.")
                    except s3_client.exceptions.ClientError as e:
                        if e.response['Error']['Code'] == '404':
                            # File does not exist, proceed with upload
                            s3_client.upload_file(file_path, aws_bucket, s3_path)
                            logging.info(f'Uploaded {file_path} to s3://{aws_bucket}/{s3_path}.')
                        else:
                            logging.exception(f"Unexpected error during upload of {file_path}")
                            upload_failed = True
                            raise
                # If any failure happened during the transfer process, log the failure for the whole directory
            if upload_failed:
                logging.error(f"Failed to transfer some or all files in {bag_dir} to S3.")
                failed_uploads += 1
            else:
                logging.info(f"Successfully transferred entire bag directory {bag_dir} to S3.")
                successful_uploads += 1

        except Exception as e:
            logging.exception(f"Error transferring directory {bag_dir} to S3: {e}")
            failed_uploads += 1

    def s3_key_construction(aws_bucket, refid, collection_id):
        """Constructs the S3 key for the given bucket, refid, and collection_id."""
        base_s3_path = config.get('base_s3_path', '')  # Fetch the base path from config
        # Construct the S3 key
        s3_key = os.path.join(base_s3_path, collection_id, refid).replace("\\", "/")
        return s3_key
    
    def construct_s3_cloudfront_URI(s3_path):
        '''
        Takes an S3 prefix (the S3 key without the bucket name) and returns a CloudFront URI. 
        '''
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
            refids.append(folder)  # Assuming folder name is the refid
    return refids

def create_bag_and_upload(bag_dir: Path, rights_ids: list):
    """Creates a BagIt bag from a directory and its metadata."""
    global successful_bags, failed_bags
    try:
        # Check if the directory exists and has files
        if not bag_dir.exists() or not any(bag_dir.iterdir()):
            logging.error(f"The directory {bag_dir} is empty or doesn't exist.")
            failed_bags += 1
            return
        
        # Fetch the URI from ArchivesSpace based on refid (folder name)
        refid = bag_dir.name  # Assuming folder name is the refid
        obj_uri = aspace_ops.uri_from_refid(refid)

        # Fetch the dates closest to the record (move up the archival description tree until it finds a record with a date).
        dates_array = find_closest_value(obj_uri,'dates',as_client)
        #print(dates_array) #debug

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

        # Create the Bag
        bagit.make_bag(bag_dir, metadata, checksum=['sha256'])
        logging.info(f'Bag created from {bag_dir}.')
        successful_bags += 1

        # Construct the S3 key
        s3_key = S3handler.s3_key_construction(config['aws_bucket'], refid, collection_id)

        # Transfer the bag to S3
        S3handler.transfer_to_s3(bag_dir, s3_key)

        #return the s3_key for use in DAO record creation
        return s3_key

    except Exception as e:
        logging.exception(f"Error creating bag for {bag_dir}: {str(e)}")
        failed_bags += 1

if __name__ == "__main__":
    input_directory = config['input_directory']
    rights_ids = config.get('rights_ids', [])

    # Fetch all refids (folder names) from the input directory
    refids = get_refids(input_directory)

    # Create an instance of the aspaceOperations class
    aspace_ops = aspaceOperations()

    for refid in refids:
        try:
            logging.info(f"starting {refid}")
            bag_dir = Path(input_directory) / refid
            s3_key = create_bag_and_upload(bag_dir, rights_ids)
            #if the create_bag_and_upload doesn't return a s3 key, then we don't need to advance further with the workflow
            if s3_key is None:
                break
            file_uri = S3handler.construct_s3_cloudfront_URI(s3_key)
            aspace_ops.create_preservation_dao(file_uri,refid)
        except Exception as e:
            logging.error(f"Error processing {refid}: {e}")

logging.info(f"Summary: {successful_uploads} successful uploads, {failed_uploads} failed uploads.")
logging.info(f"Summary: {successful_bags} successful bags, {failed_bags} failed bags.")
