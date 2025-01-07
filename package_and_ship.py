import os
import logging
from pathlib import Path
import bagit
from asnake.aspace import ASpace
from asnake.utils import find_closest_value
from dateutil import parser
from dateutil.relativedelta import relativedelta
from user.config import config
import boto3

# Set up logging
logging.basicConfig(level=logging.INFO)

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

# Initialize the client
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
            start_dates.append(date['begin'])
            if date['date_type'] == 'single':
                end_dates.append(date['begin'])
            else:
                end_dates.append(date['end'])
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
        start_date, end_date = self.get_date_range(dates_array)
        formatted_start_date, formatted_end_date = self.format_aspace_date(start_date, end_date)
        return formatted_start_date, formatted_end_date

def uri_from_refid(refid):
    """Fetch the URI of an archival object from ArchivesSpace by its ref_id."""
    try:
        # Use dictionary-style access to get the aspace_repo value from the config
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

def get_refids(input_directory):
    """Fetch the refids (folder names) from the given directory."""
    refids = []
    for folder in os.listdir(input_directory):
        folder_path = os.path.join(input_directory, folder)
        if os.path.isdir(folder_path):
            refids.append(folder)  # Assuming folder name is the refid
    return refids

def get_closest_date(obj_uri):
    """Fetch the closest date associated with an archival object."""
    try:
        # Use find_closest_value to extract the closest match for dates
        closest_date = find_closest_value(obj_uri, 'dates', as_client)

        if closest_date:
            logging.info(f"Found closest date for URI {obj_uri}: {closest_date}")
            return closest_date
        else:
            logging.warning(f"No date found for URI {obj_uri}.")
            return None

    except Exception as e:
        logging.error(f"Error fetching closest date for URI {obj_uri}: {str(e)}")
        raise

def get_collection_id(obj_uri):
    """Fetches the collection_id from an archival object URI in ArchivesSpace.
    I'm not sure how well this will handle atypical collection-ids like MS-UA collections or Corcoran.
    May need to create a dictionary to match these to the desired output.
    """
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

def create_bag(bag_dir: Path, rights_ids: list):
    """Creates a BagIt bag from a directory and its metadata."""
    try:
        # Check if the directory exists and has files
        if not bag_dir.exists() or not any(bag_dir.iterdir()):
            logging.error(f"The directory {bag_dir} is empty or doesn't exist.")
            return

        # Fetch the URI from ArchivesSpace based on refid (folder name)
        refid = bag_dir.name  # Assuming folder name is the refid
        obj_uri = uri_from_refid(refid)

        # Fetch the metadata for the archival object using the URI
        obj_metadata = as_client.get(obj_uri).json()
        dates_array = obj_metadata.get('dates', [])

        # Process the dates
        aspace_date_formatter = ASpaceDateFormatter()
        formatted_start_date, formatted_end_date = aspace_date_formatter.process_dates(dates_array)

        # Fetch the collection_id using the new function
        collection_id = get_collection_id(obj_uri)

        # Default to empty rights IDs if none are provided
        if not rights_ids:
            logging.warning("No rights IDs provided. Defaulting to empty.")
            rights_ids = ['']

        # Create metadata with the URI, closest date, and other required fields
        metadata = {
            'ArchivesSpace-URI': obj_uri,
            'Start-Date': formatted_start_date,
            'End-Date': formatted_end_date,
            'Origin': 'digitization',
            'Rights-ID': '',
            'Collection-ID': collection_id,
            'BagIt-Profile-Identifier': 'scrc-digitization-profile.json'
        }

        # Create the BagIt bag
        bagit.make_bag(bag_dir, metadata, checksum=['sha256'])
        logging.info(f'Bag created from {bag_dir} with Rights IDs {rights_ids}.')

        # Construct the S3 key
        s3_key = s3_key_construction(config['aws_bucket'], refid, collection_id)

        # Transfer the bag to S3
        transfer_to_s3(bag_dir, s3_key)

    except Exception as e:
        logging.error(f"Error creating bag for {bag_dir}: {str(e)}")

def s3_key_construction(aws_bucket, refid, collection_id):
    """Constructs the S3 key for the given bucket, refid, and collection_id."""
    base_s3_path = config.get('base_s3_path', '')  # Fetch the base path from config
    # Construct the S3 key
    s3_key = os.path.join(base_s3_path, collection_id, refid).replace("\\", "/")
    return s3_key

def transfer_to_s3(bag_dir: Path, s3_key: str):
    """Transfers the created bag to the specified S3 location."""
    try:
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
                        # Unexpected error, re-raise
                        raise

    except Exception as e:
        logging.error(f"Error transferring directory {bag_dir} to S3: {str(e)}")

if __name__ == "__main__":
    input_directory = config['input_directory']
    rights_ids = config.get('rights_ids', [])

    # Fetch all refids (folder names) from the input directory
    refids = get_refids(input_directory)

    for refid in refids:
        # Create a BagIt bag for each refid
        bag_dir = Path(input_directory) / refid
        create_bag(bag_dir, rights_ids)
