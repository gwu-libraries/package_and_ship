# Package and Ship
Packages and ingests digital collections materials into SCRC digcol storage. 

* Bags content with relevant descrptive metadata retrieved via the ArchivesSpace API. 
* Places the content in the relevant "collection" level directory in dig-col storage
* Creates digital archival object records and links them to the relevant archival object record. The DAO records hold file versions that point to the content in dig-col storage (via cloudfront).

# Structure of Files
Each "object" must be placed in a directory that is titled with the ArchivesSpace refid of that object. Select the root directory in the config file; the script will run over every "ref-id" folder in the root directory. 

<pre>
root_folder/
├── ref_id/
│   ├── audio_file_1.wav
│   ├── audio_file_2.wav
│   └── derivatives/
│       ├── audio_file_1.mp3
│       ├── audio_file_1_caption_eng.vtt
│       ├── audio_file_2.mp3
│       └── audio_file_2_caption_eng.vtt
├── ref_id2/
│   ├── audio_file_1.wav
│   └── derivatives/
│       ├── audio_file_1.mp3
│       └── audio_file_1_caption_eng.vtt
└── ref_id3/
    ├── audio_file_1.wav
    └── derivatives/
        ├── audio_file_1.mp3
        └── audio_file_1_caption_eng.vtt
 </pre>

 # CLI Options

- -r, --refid: Name of a specific folder/ref_id inside your input directory to process a single package instead of running batch mode.
- -b, --born-digital: Sets bag profile/origin to born-digital and triggers ArchivesSpace extent and scope note updates.
- -a, --accession: Optional accession number string (e.g., 2026-023). Appends Accession-Number to bag-info.txt.


# Example Uses

1. Run batch processing on all standard folders in input directory. Use for digitized content only.
```
python package_and_ship.py
```

2. Process a single folder in input directory. Use for digitized content only.
```
python package_and_ship.py -r <ref_id>
```

3. Process a single born-digital package with an accession number:

```
python package_and_ship.py -r ref_67890 -b -a 2026-023
```

4. Run batch processing on all folders as born-digital with an accession number:
```
python package_ship.py -b -a 2026-023
```

