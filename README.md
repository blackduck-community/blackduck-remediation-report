# blackduck-remediation-report
This script is used to export remediation status of the vulnerabilities from Black Duck SCA.

By default the script is exporting the status for all projects in given
Black Duck instance. There are some filtering implemented to limit the 
project count.

Will need following: Python 3 and module: blackduck, jinja2, pdfkit, tinydb and BetterJSONStorage
To install the needed modules: 

pip install blackduck jinja2 pdfkit tinydb BetterJSONStorage
OR
pip install -r requirements.txt

Pdfkit requires that you have wkhtmltopdf installed and in your path. (https://wkhtmltopdf.org/)

This script is using template: BD_Results_Distribution_by_Triage_Status_v3.html. This template must be in templates -folder.

## Usage:
To run HTML and PDF report for all projects in Black Duck -instance
```python
python blackduck_triage_extract.py --token="<ACCESS_TOKEN>" --url="<BD_URL>" --html --pdf --json
```

To collect metrics for all projects in Black Duck by using the cache
```python
python blackduck_triage_extract.py --token="<ACCESS_TOKEN>" --url="<BD_URL>" --cache --html --pdf --json
```

To run HTML and PDF report for all projects in given project group. This will collect all projects from given project group and also all projects from sub project groups recursively.
```python
python blackduck_triage_extract.py --token="<ACCESS_TOKEN>" --url="<BD_URL>" --project_group_name="<PROJECT_GROUP_NAME>" --html --pdf
```

To run HTML and PDF report for given project and version
```python
python blackduck_triage_extract.py --token="<ACCESS_TOKEN>" --url="<BD_URL>" --project="<PROJECT_NAME>" --version="<PROJECT_VERSION_NAME>" --html --pdf
```

To limit projects based on project version phases DEVELOPMENT and PLANNING. Options are: PLANNING,DEVELOPMENT,RELEASED,DEPRECATED,ARCHIVED,PRERELEASE
```python
python blackduck_triage_extract.py --token="<ACCESS_TOKEN>" --url="<BD_URL>" --phaseCategories="PLANNING,DEVELOPMENT" --html --pdf
```

To limit projects based on project version distribution EXTERNAL and create only HTML report. Options are: EXTERNAL,SAAS,INTERNAL,OPENSOURCE
```python
python blackduck_triage_extract.py --token="<ACCESS_TOKEN>" --url="<BD_URL>" --distributionCategories="EXTERNAL" --html
```

By default all reports are written in the current folder where script is run, but if you want to change the folder, you can use --dir to give a new folder
```python
python blackduck_triage_extract.py --token="<ACCESS_TOKEN>" --url="<BD_URL>" --dir="./reports" --html --pdf
```