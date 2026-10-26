import argparse
import base64
import binascii
import logging
import re
from datetime import datetime
from pathlib import Path
import bagit
import boto3
from asnake.aspace import ASpace
from asnake.utils import find_closest_value
from boto3.s3.transfer import TransferConfig
from dateutil import parser
from dateutil.relativedelta import relativedelta
from user.config import config

# Setup logging environment
log_dir = Path("logs")
log_dir.mkdir(parents=True, exist_ok=True)

current_time = datetime.now().strftime("%Y-%m-%d_%H-%M")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(log_dir / f"package_ship_{current_time}.log", mode='a'),
        logging.StreamHandler()
    ]
)

# Global tracking stats
stats = {
    "successful_uploads": 0,
    "failed_uploads": 0,
    "successful_bags": 0,
    "failed_bags": 0
}

# Initialize ArchivesSpace client
try:
    as_client = ASpace(
        baseurl=config.get('aspace_host'),
        username=config.get('aspace_user'),
        password=config.get('aspace_pass')
    ).client
    logging.info("Connected to ArchivesSpace successfully.")
except Exception as e:
    logging.error(f"Error initializing ArchivesSpace client: {e}")
    raise

# Initialize AWS S3 client
s3_client = boto3.client(
    's3',
    aws_access_key_id=config['aws_access'],
    aws_secret_access_key=config['aws_secret'],
    region_name=config['aws_region']
)

# ------------------------------------------------------------------------------
# Born-Digital & File Analysis Helpers
# ------------------------------------------------------------------------------

def parse_accession_folder(folder_name: str):
    """
    Parses accession folder names to extract base ID and optional sub-batch suffix.
    Examples:
        'acc_1736'        -> ('1736', 'sip_01')
        'acc_1736_02'     -> ('1736', 'sip_02')
        'acc_1736_batchB' -> ('1736', 'batchB')
    """
    match = re.match(r"^acc_(\d+)(?:_(.+))?$", folder_name)
    if match:
        acc_id = match.group(1)
        sip_suffix = match.group(2) if match.group(2) else "sip_01"
        return acc_id, sip_suffix
    return None, None


def analyze_bag_contents(bag_dir: Path):
    """Calculates file count, formatted extent size details, and generates an HTML-tagged file list string."""
    data_dir = bag_dir / "data" if (bag_dir / "data").exists() else bag_dir
    
    # Exclude system files and BagIt manifest/tag files
    ignored_names = {'bag-info.txt', 'bagit.txt'}
    files = [
        f for f in data_dir.rglob('*') 
        if f.is_file() 
        and not f.name.startswith('.') 
        and not f.name.startswith('manifest-') 
        and not f.name.startswith('tagmanifest-')
        and f.name not in ignored_names
    ]

    total_bytes = sum(f.stat().st_size for f in files)
    total_files = len(files)

    # Dynamic unit selection matching ArchivesSpace controlled vocabulary values
    if total_bytes >= 1024 ** 3:
        extent_number = f"{total_bytes / (1024 ** 3):.2f}"
        extent_type = "gigabyte(s)"
    elif total_bytes >= 1024 ** 2:
        extent_number = f"{total_bytes / (1024 ** 2):.2f}"
        extent_type = "megabyte(s)"
    else:
        extent_number = f"{total_bytes / 1024:.2f}"
        extent_type = "kilobyte(s)"

    # Wrap each relative path in <p> tags for AO records
    file_list = [f.relative_to(data_dir).as_posix() for f in files]
    file_list_html = "".join([f"<p>{path}</p>" for path in sorted(file_list)])

    return total_files, extent_number, extent_type, file_list_html


def update_ao_born_digital_metadata(obj_uri: str, ao_record: dict, bag_dir: Path, accession: str = None):
    """Appends extent data, scope & content note, and optional accession info to the AO in ArchivesSpace."""
    total_files, extent_number, extent_type, file_list_html = analyze_bag_contents(bag_dir)

    # 1. Update Extents
    extents = ao_record.setdefault("extents", [])
    extents.append({
        "jsonmodel_type": "extent",
        "portion": "whole",
        "number": extent_number,
        "extent_type": extent_type,
        "container_summary": f"Total Files: {total_files}"
    })

    # 2. Append Scope and Content Note using HTML formatting tags (<p>, <b>)
    notes = ao_record.setdefault("notes", [])
    
    note_title = "Born-Digital File Inventory"
    if accession:
        note_title += f" (Accession {accession})"

    note_body = (
        f"<p><b>Digital File List:</b></p>"
        f"{file_list_html}"
    )

    file_list_note = {
        "jsonmodel_type": "note_multipart",
        "type": "scopecontent",
        "publish": True,
        "title": note_title,
        "subnotes": [
            {
                "jsonmodel_type": "note_text",
                "content": note_body,
                "publish": True
            }
        ]
    }
    notes.append(file_list_note)

    # 3. Post Updated AO Record
    try:
        response = as_client.post(obj_uri, json=ao_record)
        if response.status_code == 200:
            logging.info(f"Successfully updated AO metadata and file list note for {obj_uri}")
            return response.json()
        else:
            logging.error(f"Failed to update AO metadata for {obj_uri}: {response.text}")
            return ao_record
    except Exception as e:
        logging.error(f"Error posting updated AO record for {obj_uri}: {e}")
        return ao_record


def update_accession_born_digital_metadata(acc_uri: str, acc_record: dict, bag_dir: Path, sip_id: str = "sip_01"):
    """Appends extent summary data for a specific SIP transfer to an Accession record."""
    total_files, extent_number, extent_type, _ = analyze_bag_contents(bag_dir)

    # Appends new extent entry for this transfer
    extents = acc_record.setdefault("extents", [])
    extents.append({
        "jsonmodel_type": "extent",
        "portion": "part",
        "number": extent_number,
        "extent_type": extent_type,
        "container_summary": f"Transfer ({sip_id}): {total_files} files"
    })

    try:
        response = as_client.post(acc_uri, json=acc_record)
        if response.status_code == 200:
            logging.info(f"Updated Accession extent metadata for {acc_uri} [{sip_id}] ({total_files} files, {extent_number} {extent_type})")
            return response.json()
        else:
            logging.error(f"Failed updating Accession at {acc_uri}: {response.text}")
            return acc_record
    except Exception as e:
        logging.error(f"Error posting updated Accession record for {acc_uri}: {e}")
        return acc_record


# ------------------------------------------------------------------------------
# ArchivesSpace & Date Helpers
# ------------------------------------------------------------------------------

def parse_aspace_dates(dates_array):
    """Extracts and formats date ranges from an ArchivesSpace date array."""
    if not dates_array:
        logging.warning("No date array provided. Using default date range: 1900-01-01 to 9999-12-31.")
        return '1900-01-01', '9999-12-31'

    starts, ends = [], []
    for d in dates_array:
        begin = d.get('begin')
        if not begin or str(begin).lower() == 'undated':
            continue
        starts.append(begin)
        
        if d.get('date_type') == 'single':
            ends.append(begin)
        elif d.get('end'):
            ends.append(d['end'])
        else:
            ends.append(begin)

    if not starts or not ends:
        logging.warning("No valid date range found. Using default date range: 1900-01-01 to 9999-12-31.")
        return '1900-01-01', '9999-12-31'

    start_str, end_str = sorted(starts)[0], sorted(ends)[-1]
    parsed_start = parser.isoparse(start_str)
    parsed_end = parser.isoparse(end_str)

    formatted_start = parsed_start.strftime('%Y-%m-%d')
    if len(end_str) == 4:
        formatted_end = (parsed_end + relativedelta(month=12, day=31)).strftime('%Y-%m-%d')
    elif len(end_str) == 7:
        formatted_end = (parsed_end + relativedelta(day=31)).strftime('%Y-%m-%d')
    else:
        formatted_end = end_str

    return formatted_start, formatted_end


def get_ao_record_by_refid(refid):
    """Fetches the URI and JSON payload for an Archival Object by ref_id."""
    url = f"repositories/{config['aspace_repo']}/find_by_id/archival_objects?ref_id[]={refid}"
    res = as_client.get(url).json()
    results = res.get("archival_objects", [])
    
    if len(results) != 1:
        raise ValueError(f"Expected 1 record for refid {refid}, found {len(results)}")
    
    uri = results[0]['ref']
    ao_record = as_client.get(uri).json()
    return uri, ao_record


def get_accession_record_by_id(accession_id: str):
    """Fetches the URI and JSON payload for an Accession using its database integer ID."""
    repo_id = config['aspace_repo']
    endpoint = f"repositories/{repo_id}/accessions/{accession_id}"
    
    response = as_client.get(endpoint)
    if response.status_code == 200:
        acc_record = response.json()
        return acc_record['uri'], acc_record
    else:
        raise ValueError(f"Failed to fetch Accession ID {accession_id}: [{response.status_code}] {response.text}")


def create_preservation_dao(file_uri, refid):
    """Creates a DAO record in ArchivesSpace and links it to the archival object."""
    obj_uri, ao_record = get_ao_record_by_refid(refid)

    # Calculate unique digital object ID
    existing_ids = set()
    for instance in ao_record.get("instances", []):
        if instance.get("instance_type") == "digital_object":
            dao_ref = instance['digital_object']['ref']
            dao_json = as_client.get(dao_ref).json()
            existing_ids.add(dao_json.get('digital_object_id'))

    new_dao_id = refid
    while new_dao_id in existing_ids:
        logging.warning(f"Digital object ID {new_dao_id} already exists. Appending _presCopy.")
        new_dao_id = f"{new_dao_id}_presCopy"

    dao_payload = {
        "jsonmodel_type": "digital_object",
        "publish": True,
        "title": ao_record.get('display_string', refid),
        "digital_object_id": new_dao_id,
        "file_versions": [{
            "file_uri": file_uri,
            "publish": False,
            "use_statement": "access_staff"
        }]
    }

    try:
        dao_resp = as_client.post(f"repositories/{config['aspace_repo']}/digital_objects", json=dao_payload).json()
        dao_ref = dao_resp["uri"]
        logging.info(f"Created new DAO: {dao_ref}")

        instances = ao_record.setdefault("instances", [])
        instances.append({"instance_type": "digital_object", "digital_object": {"ref": dao_ref}})
        as_client.post(obj_uri, json=ao_record)
        logging.info(f"Linked new DAO {dao_ref} to AO {obj_uri}")
    except Exception as e:
        logging.error(f"Error creating or linking DAO for {refid}: {e}")


def create_accession_dao(file_uri: str, accession_id: str, sip_id: str = "sip_01"):
    """Creates a distinct Digital Object for each SIP linked to the parent Accession."""
    acc_uri, acc_record = get_accession_record_by_id(accession_id)
    dao_identifier = f"acc_{accession_id}_{sip_id}"

    dao_payload = {
        "jsonmodel_type": "digital_object",
        "publish": True,
        "title": f"{acc_record.get('title', 'Accession ' + accession_id)} ({sip_id.upper()})",
        "digital_object_id": dao_identifier,
        "file_versions": [{
            "file_uri": file_uri,
            "publish": False,
            "use_statement": "access_staff"
        }]
    }

    try:
        dao_resp = as_client.post(f"repositories/{config['aspace_repo']}/digital_objects", json=dao_payload).json()
        dao_ref = dao_resp["uri"]
        logging.info(f"Created DAO for Accession {accession_id} [{sip_id}]: {dao_ref}")

        instances = acc_record.setdefault("instances", [])
        instances.append({
            "instance_type": "digital_object", 
            "digital_object": {"ref": dao_ref}
        })
        
        as_client.post(acc_uri, json=acc_record)
        logging.info(f"Linked DAO {dao_ref} ({dao_identifier}) to Accession {acc_uri}")
    except Exception as e:
        logging.error(f"Error creating or linking DAO for Accession {accession_id}: {e}")


# ------------------------------------------------------------------------------
# S3 & Integrity Helpers
# ------------------------------------------------------------------------------

def load_bag_checksums(bag_dir: Path):
    """Parses SHA256 manifests from the bag into a relative path lookup dictionary."""
    checksums = {}
    for manifest_name in ['manifest-sha256.txt', 'tagmanifest-sha256.txt']:
        manifest_path = bag_dir / manifest_name
        if manifest_path.exists():
            with open(manifest_path, 'r', encoding='utf-8') as f:
                for line in f:
                    parts = line.strip().split(maxsplit=1)
                    if len(parts) == 2:
                        hex_hash, rel_path = parts
                        checksums[rel_path.strip().replace('\\', '/')] = hex_hash
    return checksums


def construct_s3_key(folder_name: str, collection_id: str = None, accession_id: str = None, sip_id: str = None) -> str:
    """
    Constructs target S3 path prefix for uploads.
    - Unprocessed accessions: 'accessions_unprocessed/{accession_id}/{sip_id}'
    - Processed archival objects: '{base_s3_path}/{collection_id}/{ref_id}'
    """
    if folder_name.startswith("acc_"):
        return f"accessions_unprocessed/{accession_id}/{sip_id}"
    
    base_prefix = config.get("base_s3_path", "").strip("/")
    target_path = f"{collection_id}/{folder_name}"

    if base_prefix:
        return f"{base_prefix}/{target_path}"
    return target_path


def transfer_to_s3(bag_dir: Path, s3_key: str):
    """Transfers local files in bag_dir to S3 using multi-part transfer and checksum metadata."""
    aws_bucket = config['aws_bucket']
    if config.get('dry_run'):
        logging.info(f"[Dry Run] S3 Transfer: {bag_dir} -> {aws_bucket}/{s3_key}")
        return

    transfer_config = TransferConfig(
        multipart_threshold=100 * 1024 * 1024,
        max_concurrency=10,
        multipart_chunksize=25 * 1024 * 1024,
        use_threads=True
    )

    checksums = load_bag_checksums(bag_dir)
    upload_failed = False

    for file_path in bag_dir.rglob('*'):
        if file_path.is_dir():
            continue

        rel_path = file_path.relative_to(bag_dir).as_posix()
        s3_path = f"{s3_key}/{rel_path}".strip('/')

        try:
            # Check existence
            try:
                s3_client.head_object(Bucket=aws_bucket, Key=s3_path)
                logging.warning(f"File {s3_path} already exists on S3. Skipping.")
                continue
            except s3_client.exceptions.ClientError as e:
                if e.response['Error']['Code'] != '404':
                    raise

            file_size = file_path.stat().st_size
            extra_args = {'ChecksumAlgorithm': 'SHA256'}

            if rel_path in checksums:
                hex_hash = checksums[rel_path]
                b64_hash = base64.b64encode(binascii.unhexlify(hex_hash)).decode('utf-8')
                extra_args['Metadata'] = {'bagit-sha256': hex_hash}

                if file_size < transfer_config.multipart_threshold:
                    extra_args['ChecksumSHA256'] = b64_hash
                    logging.info(f"Uploading {rel_path} (Strict Checksum + Metadata)")
                else:
                    logging.info(f"Uploading {rel_path} (Multipart - Metadata added)")
            else:
                logging.info(f"Uploading {rel_path} (Calculated on fly)")

            s3_client.upload_file(
                str(file_path),
                aws_bucket,
                s3_path,
                Config=transfer_config,
                ExtraArgs=extra_args
            )

        except s3_client.exceptions.ClientError as e:
            if any(err in str(e) for err in ["BadDigest", "ChecksumMismatch"]):
                logging.error(f"CRITICAL: Integrity failure for {file_path}.")
            else:
                logging.error(f"AWS Error uploading {file_path}: {e}")
            upload_failed = True
        except Exception as e:
            logging.exception(f"Unexpected error uploading {file_path}: {e}")
            upload_failed = True

    if upload_failed:
        stats["failed_uploads"] += 1
    else:
        stats["successful_uploads"] += 1


def construct_cloudfront_uri(s3_path):
    """Constructs a CloudFront inventory viewer URL from an S3 key."""
    base_uri = config.get('cloudfront_base_URI', '')
    return f"{base_uri}inventory.html?folder={s3_path}"


# ------------------------------------------------------------------------------
# Core Execution Pipeline
# ------------------------------------------------------------------------------

def create_bag_and_upload(bag_dir: Path, dry_run=False, born_digital=False, accession=None):
    """Generates BagIt package, uploads contents to S3, and updates ArchivesSpace metadata/DAOs."""
    if not bag_dir.exists() or not any(bag_dir.iterdir()):
        logging.error(f"Directory {bag_dir} is empty or missing.")
        stats["failed_bags"] += 1
        return None

    folder_name = bag_dir.name
    origin = 'born-digital' if born_digital else 'digitization'
    profile_id = 'scrc-born-digital-profile.json' if born_digital else 'scrc-digitization-profile.json'

    # --------------------------------------------------------------------------
    # Route 1: Unprocessed Accession Folder (prefix acc_)
    # --------------------------------------------------------------------------
    if folder_name.startswith("acc_"):
        accession_id, sip_id = parse_accession_folder(folder_name)
        if not accession_id:
            logging.error(f"Could not parse accession ID from folder name: {folder_name}")
            stats["failed_bags"] += 1
            return None

        acc_uri, acc_record = get_accession_record_by_id(accession_id)

        metadata = {
            'ArchivesSpace-URI': acc_uri,
            'Title': f"{acc_record.get('title', 'Accession ' + accession_id)} ({sip_id.upper()})",
            'Origin': origin,
            'BagIt-Profile-Identifier': profile_id,
            'SIP-Identifier': sip_id
        }
        if accession:
            metadata['Accession-Number'] = accession

        if born_digital and not dry_run:
            logging.info(f"Updating Accession {accession_id} with born-digital extent metadata for {sip_id}...")
            acc_record = update_accession_born_digital_metadata(acc_uri, acc_record, bag_dir, sip_id=sip_id)

        if dry_run:
            logging.info(f"[Dry Run] Skipping Bag creation for Accession {folder_name}. Metadata: {metadata}")
        else:
            bagit.make_bag(str(bag_dir), metadata, checksum=['sha256'])
            logging.info(f"Bag created for Accession {accession_id} [{sip_id}] (Origin: {origin}).")
            stats["successful_bags"] += 1

        s3_key = construct_s3_key(folder_name, accession_id=accession_id, sip_id=sip_id)

        if dry_run:
            logging.info(f"[Dry Run] Skipping S3 upload for {bag_dir}. S3 Key would be: {s3_key}")
        else:
            transfer_to_s3(bag_dir, s3_key)
            file_uri = s3_key
            create_accession_dao(file_uri, accession_id, sip_id=sip_id)

        return s3_key

    # --------------------------------------------------------------------------
    # Route 2: Archival Object Folder (standard ref_id)
    # --------------------------------------------------------------------------
    else:
        refid = folder_name
        obj_uri, ao_record = get_ao_record_by_refid(refid)

        dates_array = find_closest_value(obj_uri, 'dates', as_client)
        formatted_start, formatted_end = parse_aspace_dates(dates_array)

        res_ref = ao_record.get('resource', {}).get('ref', '')
        collection_id = as_client.get(res_ref).json().get('id_0', '').lower() if res_ref else ''

        metadata = {
            'ArchivesSpace-URI': obj_uri,
            'Start-Date': formatted_start,
            'Title': ao_record.get('title'),
            'End-Date': formatted_end,
            'Origin': origin,
            'Rights-ID': '',
            'Collection-ID': collection_id,
            'BagIt-Profile-Identifier': profile_id
        }
        if accession:
            metadata['Accession-Number'] = accession

        if born_digital and not dry_run:
            logging.info(f"Updating Archival Object {refid} with born-digital extent and file inventory note...")
            ao_record = update_ao_born_digital_metadata(obj_uri, ao_record, bag_dir, accession=accession)

        if dry_run:
            logging.info(f"[Dry Run] Skipping Bag creation for {bag_dir}. Metadata: {metadata}")
        else:
            bagit.make_bag(str(bag_dir), metadata, checksum=['sha256'])
            logging.info(f"Bag created from {bag_dir} (Origin: {origin}).")
            stats["successful_bags"] += 1

        s3_key = construct_s3_key(folder_name, collection_id=collection_id)

        if dry_run:
            logging.info(f"[Dry Run] Skipping S3 upload for {bag_dir}. S3 Key would be: {s3_key}")
        else:
            transfer_to_s3(bag_dir, s3_key)
            file_uri = construct_cloudfront_uri(s3_key) if 'scrc-digcol' in config.get('aws_bucket', '') else s3_key
            create_preservation_dao(file_uri, refid)

        return s3_key


def parse_cli_args():
    """Parses command-line arguments for single-folder or batch processing."""
    parser = argparse.ArgumentParser(
        description="Packager and Shipper pipeline for ArchivesSpace and S3."
    )
    parser.add_argument(
        '-r', '--refid', 
        type=str, 
        help="Target a single folder name (e.g. 'scrc_ref_001', 'acc_1736', 'acc_1736_02') to process instead of batch mode."
    )
    parser.add_argument(
        '-b', '--born-digital',
        action='store_true',
        help="Set BagIt metadata Origin to 'born-digital', update profile identifier, and attach extent details."
    )
    parser.add_argument(
        "-a", "--accession",
        type=str,
        default=None,
        help="Optional accession number string associated with the transfer (e.g., 2026-023)"
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_cli_args()
    input_directory = Path(config['input_directory'])
    dry_run = config.get('dry_run', False)

    if args.refid:
        target_path = input_directory / args.refid
        if target_path.exists() and target_path.is_dir():
            logging.info(f"Targeted execution: Processing single folder '{args.refid}'")
            folders = [args.refid]
        else:
            logging.error(f"Directory '{args.refid}' does not exist at {target_path}")
            folders = []
    else:
        logging.info("Batch execution: Processing all directories in input_directory.")
        folders = [d.name for d in input_directory.iterdir() if d.is_dir()]

    for folder_name in folders:
        try:
            logging.info(f"Starting pipeline execution for folder: {folder_name}")
            bag_dir = input_directory / folder_name
            s3_key = create_bag_and_upload(
                bag_dir, 
                dry_run=dry_run, 
                born_digital=args.born_digital,
                accession=args.accession
            )
            if dry_run:
                logging.info(f"[Dry Run] Completed dry-run checks for {folder_name}.")

        except Exception as e:
            logging.error(f"Failed processing folder {folder_name}: {e}")

    # Summary reporting
    logging.info("========================================")
    logging.info("EXECUTION SUMMARY")
    logging.info(f"Bags Created / Validated: {stats['successful_bags']} success, {stats['failed_bags']} failed")
    logging.info(f"S3 Transfers:             {stats['successful_uploads']} success, {stats['failed_uploads']} failed")
    logging.info("========================================")