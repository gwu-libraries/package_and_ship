# Package and Ship
Packages and ingests digitized material into dig-col storage.

* Bags content with relevant descrptive metadata retrieved from ArchivesSpace via the ArchivesSpace API
* Places the content in the relevant "collection" level directory in dig-col storage
* Creates digital archival object records and links them to the relevant archival object record. The DAO records hold file versions that point to the content in dig-col storage (via cloudfront).

# How to Use 
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

# Example Bag

<pre>ArchivesSpace-URI: /repositories/2/archival_objects/582952
Bag-Software-Agent: bagit.py v1.8.1 <https://github.com/LibraryOfCongress/bagit-python>
BagIt-Profile-Identifier: scrc-digitization-profile.json
Bagging-Date: 2025-01-28
Collection-ID: ibt0084
End-Date: 1962-12-31
Origin: digitization
Payload-Oxum: 4695087227.3
Rights-ID: 
Start-Date: 1962-01-01
Title:: Congress Speaks at the 19th Convention: Senator McGee
</pre>
