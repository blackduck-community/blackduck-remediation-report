# -*- coding: utf-8 -*-
'''
This script is used to export remediation status for given level.

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

BD_report:
├─templates
    ├─BD_Results_Distribution_by_Triage_Status_v3.html
├─blackduck_triage_extract.py
├─requirements.txt

To get AccessToken, use your Internet browser and go to:
<BD_URL>/api/current-user/tokens?limit=100&offset=0
From there click "+ Create Token" -button and give the name and Scope: "Read and Write Access" and click "Create" -button.
Then copy&paste the given accesstoken. 
NOTE: After you click "Close" -button, you cannot see the token anymore.

Usage:
#To run HTML and PDF report for all projects in Black Duck -instance
python blackduck_triage_extract.py --token="<ACCESS_TOKEN>" --url="<BD_URL>" --html --pdf --json

#To collect metrics for all projects in Black Duck by using the cache
python blackduck_triage_extract.py --token="<ACCESS_TOKEN>" --url="<BD_URL>" --cache --html --pdf --json

#To run HTML and PDF report for all projects in given project group. This will collect all projects from given project group and
#also all projects from sub project groups recursively.
python blackduck_triage_extract.py --token="<ACCESS_TOKEN>" --url="<BD_URL>" --project_group_name="<PROJECT_GROUP_NAME>" --html --pdf

#To run HTML and PDF report for given project and version
python blackduck_triage_extract.py --token="<ACCESS_TOKEN>" --url="<BD_URL>" --project="<PROJECT_NAME>" --version="<PROJECT_VERSION_NAME>" --html --pdf

#To limit projects based on project version phases DEVELOPMENT and PLANNING. Options are: PLANNING,DEVELOPMENT,RELEASED,DEPRECATED,ARCHIVED,PRERELEASE
python blackduck_triage_extract.py --token="<ACCESS_TOKEN>" --url="<BD_URL>" --phaseCategories="PLANNING,DEVELOPMENT" --html --pdf

#To limit projects based on project version distribution EXTERNAL and create only HTML report. Options are: EXTERNAL,SAAS,INTERNAL,OPENSOURCE
python blackduck_triage_extract.py --token="<ACCESS_TOKEN>" --url="<BD_URL>" --distributionCategories="EXTERNAL" --html

#By default all reports are written in the current folder where script is run, but if you want to change the folder, you can use --dir to give a new folder
python blackduck_triage_extract.py --token="<ACCESS_TOKEN>" --url="<BD_URL>" --dir="./reports" --html --pdf

If Proxy is needed, you can use export method.
#Example:
export HTTP_PROXY='http://10.10.10.10:8000'
export HTTPS_PROXY='https://10.10.10.10:1212'

NOTE: You can set token and url parameters as an environment variable:
export BD_TOKEN="<BD_TOKEN>"
export BD_URL="<BD_URL>"

Version History:
0.1.5 - Added usage of TinyDB (https://tinydb.readthedocs.io/en/latest/index.html) for caching BD metrics.
0.1.6 - Added triangle icon in front of project version, if project version last scanning date is older than given threshold (--sinceDays). Default is 30 days.
      - Added Last scanned -date for project versions
0.1.7 - Change to use BetterJSONStorage to improve performance and reduce the database size.
0.1.8 - Added check if project has updated compared last run project.updatedAt has changed, if it has the project info will be updated into cache.
0.1.9 - Added feature to export report as in JSON -format by adding --json.
0.1.10 - Added progressbar by using tqdm to show progress of projects analysis phases
0.1.11 - Added NOT_AFFECTED remediation type and removed BetterJSONStorage usage
'''
import logging
import argparse
import sys
from blackduck.HubRestApi import HubInstance
from timeit import default_timer as timer
import jinja2
from datetime import datetime
import pdfkit
import requests
import os
import json
from tinydb import TinyDB, Query
from pathlib import Path
from tqdm import tqdm
import pandas as pd


__author__ = "Jouni Lehto"
__versionro__="0.1.11"

#Global variables
args = "" 
MAX_LIMIT=1000
templatesDir = "./templates"
templateFile = "BD_Results_Distribution_by_Triage_Status_v3.html"
db = None

def get_project_group_projects(hub):
    projects = {"totalCount": 0, "items": []}
    url = f'{hub.get_urlbase()}/api/project-groups'
    headers = hub.get_headers()
    headers['Accept'] = 'application/vnd.blackducksoftware.project-detail-5+json'
    parameters={"q":f'name:{args.project_group_name}'}
    response = requests.get(url, headers=headers, params=parameters, verify = not hub.config['insecure'])
    if response.status_code == 200:
        jsondata = response.json()
        if int(jsondata["totalCount"]) > 0:
            for projectGroup in jsondata["items"]:
                get_project_groups_children_projects(hub, projectGroup, projects, headers)
    return projects

def get_project_groups_children_projects(hub, projectGroup, projects, headers):
    parameters={"limit": MAX_LIMIT}
    response = requests.get(projectGroup['_meta']['href']+"/children", headers=headers, params=parameters, verify = not hub.config['insecure'])
    if response.status_code == 200:
        childrens = response.json()
        if int(childrens["totalCount"]) > MAX_LIMIT:
            downloaded = MAX_LIMIT
            while int(childrens["totalCount"]) > downloaded:
                parameters={"offset": downloaded, "limit": MAX_LIMIT}
                moreProjects = requests.get(projectGroup['_meta']['href']+"/children", headers=headers, params=parameters, verify = not hub.config['insecure'])
                childrens["items"] = childrens["items"] + moreProjects.json()["items"]
                downloaded += MAX_LIMIT
        if int(childrens["totalCount"]) > 0:
            for children in childrens["items"]:
                if children["isProject"] is False:
                    get_project_groups_children_projects(hub,children, projects, headers)
                else:
                    #This phase there will always be one project, so no need for limits
                    project_response = requests.get(children['_meta']['href'], headers=headers, verify = not hub.config['insecure'])
                    if project_response.status_code == 200:
                        children_projects = project_response.json()
                        if children_projects:
                            projectList = [children_projects]
                            projects["totalCount"] = int(projects["totalCount"]) + 1
                            projects["items"] = projects["items"] + projectList
    
def get_version_snippets(hub, projectversion):
    url = f'{projectversion}/snippet-counts'
    headers = hub.get_headers()
    headers['Accept'] = 'application/vnd.blackducksoftware.internal-1+json'
    response = requests.get(url, headers=headers, verify = not hub.config['insecure'])
    jsondata = response.json()
    return jsondata

def get_project_versions(hub, project, limit=100, parameters={}):
    # paramstring = self.get_limit_paramstring(limit)
    parameters.update({'limit': limit})
    url = project['_meta']['href'] + "/versions" + hub._get_parameter_string(parameters)
    headers = hub.get_headers()
    headers['Accept'] = 'application/vnd.blackducksoftware.internal-1+json'
    response = requests.get(url, headers=headers, verify = not hub.config['insecure'])
    jsondata = response.json()
    return jsondata

def get_version_vuln_components(hub, projectversion, limit=MAX_LIMIT):
    parameters={"limit": limit}
    url = projectversion['_meta']['href'] + "/vulnerable-bom-components"
    headers = hub.get_headers()
    headers['Accept'] = 'application/vnd.blackducksoftware.bill-of-materials-6+json'
    response = requests.get(url, headers=headers, params=parameters, verify = not hub.config['insecure'])
    jsondata = response.json()
    if int(jsondata["totalCount"]) > MAX_LIMIT:
        downloaded = MAX_LIMIT
        while int(jsondata["totalCount"]) > downloaded:
            parameters={"offset": downloaded, "limit": limit}
            moreComponents = requests.get(url, headers=headers, params=parameters, verify = not hub.config['insecure'])
            if "items" in moreComponents.json():
                jsondata["items"] = jsondata["items"] + moreComponents.json()["items"]
            downloaded += MAX_LIMIT
    return jsondata

def addFindings():
    global args, db
    hub = HubInstance(args.url, api_token=args.token, insecure=False)
    if args.project_group_name:
        projects = get_project_group_projects(hub)
    elif args.project:
        parameters={"q":"name:{}".format(args.project)}
        projects = hub.get_projects(limit=MAX_LIMIT, parameters=parameters)
    else:
        projects = hub.get_projects(limit=MAX_LIMIT)
        if int(projects["totalCount"]) > MAX_LIMIT:
            downloaded = MAX_LIMIT
            while int(projects["totalCount"]) > downloaded:
                parameters={"offset": downloaded}
                moreProjects = hub.get_projects(limit=MAX_LIMIT, parameters=parameters)
                projects["items"] = projects["items"] + moreProjects["items"]
                downloaded += MAX_LIMIT
    if projects and int(projects["totalCount"]) > 0:
        totalCounts=[]
        instanceLevelCount = {"Total": 0}
        instanceLevelCount["ProjectTotalCount"] = projects["totalCount"]
        instanceLevelCount["ProjectTotalVersionCount"] = 0
        instanceLevelCount["NEW"] = {"Total": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "LOW": 0}
        instanceLevelCount["IGNORED"] = {"Total": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "LOW": 0}
        instanceLevelCount["DUPLICATE"] = {"Total": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "LOW": 0}
        instanceLevelCount["MITIGATED"] = {"Total": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "LOW": 0}
        instanceLevelCount["NEEDS_REVIEW"] = {"Total": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "LOW": 0}
        instanceLevelCount["PATCHED"] = {"Total": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "LOW": 0}
        instanceLevelCount["REMEDIATION_COMPLETE"] = {"Total": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "LOW": 0}
        instanceLevelCount["REMEDIATION_REQUIRED"] = {"Total": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "LOW": 0}
        instanceLevelCount["NOT_AFFECTED"] = {"Total": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "LOW": 0}
        instanceLevelCount["SNIPPET"] = {"Total": 0, "unreviewed": 0, "reviewed": 0, "ignored": 0}
        logging.info(f"Total project count: {projects['totalCount']}")
        logging.info("Analyzing found projects...")
        progressBar = tqdm(total=len(projects["items"]), desc="Progress", unit="project")
        for index, project in enumerate(projects["items"]):
            if index%200 == 0:
                #renew the connection after every 200 projects
                hub = HubInstance(args.url, api_token=args.token, insecure=False)
            projectLevelCount = {}
            projectId = project["_meta"]["href"].split("/")[-1]
            projectLevelCount["projectID"] = projectId
            projectLevelCount["projectName"] = project["name"]
            projectLevelCount["updatedAt"] = project["updatedAt"]
            if args.cache:
                if not db.contains(Query()['projectID']==projectId):
                    # logging.debug(f'projectName: {project["name"]} not in cache')
                    getProjectMetrics(hub, project, projectLevelCount, instanceLevelCount)
                    db.insert(projectLevelCount)
                else:
                    #project data is already collected
                    # logging.debug(f'projectName: {project["name"]} found in cache')
                    projectLevelCount = db.get(Query()['projectID']==projectId)
                    if not projectLevelCount["updatedAt"] == project["updatedAt"]:
                        logging.debug(f'project: {project["name"]} was updated in BD -> updating cache...')
                        getProjectMetrics(hub, project, projectLevelCount, instanceLevelCount)
                        projectLevelCount["projectName"] = project["name"]
                        projectLevelCount["updatedAt"] = project["updatedAt"]
                        db.upsert(projectLevelCount)
                    else:
                        logging.debug(f'project: {project["name"]} was up to date.')
                        addToTotals(projectLevelCount, instanceLevelCount)
            else:
                getProjectMetrics(hub, project, projectLevelCount, instanceLevelCount)
            totalCounts.append(projectLevelCount)
            progressBar.update()
        progressBar.close()
        instanceLevelCount["projects"] = totalCounts
        return instanceLevelCount
    else:
        logging.info("No projects found!")

def getProjectMetrics(hub, project, projectLevelCount, instanceLevelCount):
    if args.version:
        parameters={"filter":f'{createPhaseFilterForVersions()}',"filter":f'{createDistributionFilterForVersions()}', 'q':"versionName:{}".format(args.version)}
        versions = get_project_versions(hub, project=project, limit=MAX_LIMIT, parameters=parameters)
    else:
        parameters={"filter":f'{createPhaseFilterForVersions()}',"filter":f'{createDistributionFilterForVersions()}'}
        versions = get_project_versions(hub, project=project, limit=MAX_LIMIT, parameters=parameters)
    projectLevelCount["Total"] = 0
    projectLevelCount["NEW"] = {"Total": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "LOW": 0}
    projectLevelCount["IGNORED"] = {"Total": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "LOW": 0}
    projectLevelCount["DUPLICATE"] = {"Total": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "LOW": 0}
    projectLevelCount["MITIGATED"] = {"Total": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "LOW": 0}
    projectLevelCount["NEEDS_REVIEW"] = {"Total": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "LOW": 0}
    projectLevelCount["PATCHED"] = {"Total": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "LOW": 0}
    projectLevelCount["REMEDIATION_COMPLETE"] = {"Total": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "LOW": 0}
    projectLevelCount["REMEDIATION_REQUIRED"] = {"Total": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "LOW": 0}
    projectLevelCount["NOT_AFFECTED"] = {"Total": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "LOW": 0}
    projectLevelCount["SNIPPET"] = {"Total": 0, "unreviewed": 0, "reviewed": 0, "ignored": 0}
    projectLevelCount["isDormant"] = False
    if versions and int(versions["totalCount"]) > 0:
        instanceLevelCount["ProjectTotalVersionCount"] = instanceLevelCount["ProjectTotalVersionCount"] + int(versions["totalCount"])
        projectLevelCount["projectVersionCount"] = versions["totalCount"]
        projectVersionsCounts = []
        for version in versions["items"]:
            versionLevelCounts = {}
            projectVersionId = version["_meta"]["href"].split("/")[-1]
            versionLevelCounts["versionID"] = projectVersionId
            versionLevelCounts["versionName"] = version["versionName"]
            versionLevelCounts["lastScanDate"] = getDate(version, "lastScanDate")
            versionLevelCounts["isDormant"] = False
            if args.sinceDays and args.sinceDays > 0:
                if "lastScanDate" in version:
                    versionLevelCounts["isDormant"] = isDormant(version["lastScanDate"])
                    if projectLevelCount["isDormant"] is False:
                        #Set isDormant to project level only if not set True yet
                        projectLevelCount["isDormant"] = versionLevelCounts["isDormant"]
            versionLevelCounts["phase"] = version["phase"]
            versionLevelCounts["distribution"] = version["distribution"]
            #Check if project version has snippets scan present
            snippetCounts = get_version_snippets(hub, version["_meta"]["href"])
            if snippetCounts["snippetScanPresent"]:
                projectVersionSnippetCounts = {"unreviewed": snippetCounts["unreviewedCount"], 
                                            "reviewed": snippetCounts["reviewedCount"],
                                            "ignored": snippetCounts["ignoredCount"],
                                            "Total": snippetCounts["totalCount"]}
                versionLevelCounts["snippets"] = projectVersionSnippetCounts
                #Project level snippet count
                projectLevelCount["SNIPPET"]["unreviewed"] = projectLevelCount["SNIPPET"]["unreviewed"] + snippetCounts["unreviewedCount"]
                projectLevelCount["SNIPPET"]["reviewed"] = projectLevelCount["SNIPPET"]["reviewed"] + snippetCounts["reviewedCount"]
                projectLevelCount["SNIPPET"]["ignored"] = projectLevelCount["SNIPPET"]["ignored"] + snippetCounts["ignoredCount"]
                projectLevelCount["SNIPPET"]["Total"] = projectLevelCount["SNIPPET"]["Total"] + snippetCounts["totalCount"]
                #Instance level snippet count
                instanceLevelCount["SNIPPET"]["unreviewed"] = instanceLevelCount["SNIPPET"]["unreviewed"] + snippetCounts["unreviewedCount"]
                instanceLevelCount["SNIPPET"]["reviewed"] = instanceLevelCount["SNIPPET"]["reviewed"] + snippetCounts["reviewedCount"]
                instanceLevelCount["SNIPPET"]["ignored"] = instanceLevelCount["SNIPPET"]["ignored"] + snippetCounts["ignoredCount"]
                instanceLevelCount["SNIPPET"]["Total"] = instanceLevelCount["SNIPPET"]["Total"] + snippetCounts["totalCount"]
            else:
                projectVersionSnippetCounts = {"unreviewed": 0, 
                                            "reviewed": 0,
                                            "ignored": 0,
                                            "Total": 0}
                versionLevelCounts["snippets"] = projectVersionSnippetCounts

            vulnerableComponents = get_version_vuln_components(hub=hub, projectversion=version)
            vulnerableComponentCountsByRemediationStatus = {"Total": 0}
            vulnerableComponentCountsByRemediationStatus["NEW"] = {"Total": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "LOW": 0}
            vulnerableComponentCountsByRemediationStatus["IGNORED"] = {"Total": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "LOW": 0}
            vulnerableComponentCountsByRemediationStatus["DUPLICATE"] = {"Total": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "LOW": 0}
            vulnerableComponentCountsByRemediationStatus["MITIGATED"] = {"Total": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "LOW": 0}
            vulnerableComponentCountsByRemediationStatus["NEEDS_REVIEW"] = {"Total": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "LOW": 0}
            vulnerableComponentCountsByRemediationStatus["PATCHED"] = {"Total": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "LOW": 0}
            vulnerableComponentCountsByRemediationStatus["REMEDIATION_COMPLETE"] = {"Total": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "LOW": 0}
            vulnerableComponentCountsByRemediationStatus["REMEDIATION_REQUIRED"] = {"Total": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "LOW": 0}
            vulnerableComponentCountsByRemediationStatus["NOT_AFFECTED"] = {"Total": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "LOW": 0}
            if vulnerableComponents and int(vulnerableComponents["totalCount"]) > 0:
                for vulnerableComponent in vulnerableComponents["items"]:
                    # By remediation status and by severity on project version level
                    byRemediationStatus = vulnerableComponentCountsByRemediationStatus[vulnerableComponent["vulnerabilityWithRemediation"]["remediationStatus"]]
                    byRemediationStatus["Total"] = byRemediationStatus["Total"] + 1
                    byRemediationStatus[vulnerableComponent["vulnerabilityWithRemediation"]["severity"]] = byRemediationStatus[vulnerableComponent["vulnerabilityWithRemediation"]["severity"]] + 1
                    vulnerableComponentCountsByRemediationStatus["Total"] = vulnerableComponentCountsByRemediationStatus["Total"] + 1
                    # By remediation status and by severity on project level
                    byRemediationStatus = projectLevelCount[vulnerableComponent["vulnerabilityWithRemediation"]["remediationStatus"]]
                    byRemediationStatus["Total"] = byRemediationStatus["Total"] + 1
                    byRemediationStatus[vulnerableComponent["vulnerabilityWithRemediation"]["severity"]] = byRemediationStatus[vulnerableComponent["vulnerabilityWithRemediation"]["severity"]] + 1
                    projectLevelCount["Total"] = projectLevelCount["Total"] + 1
                    # By remediation status and by severity on Black Duck instance level
                    byRemediationStatus = instanceLevelCount[vulnerableComponent["vulnerabilityWithRemediation"]["remediationStatus"]]
                    byRemediationStatus["Total"] = byRemediationStatus["Total"] + 1
                    byRemediationStatus[vulnerableComponent["vulnerabilityWithRemediation"]["severity"]] = byRemediationStatus[vulnerableComponent["vulnerabilityWithRemediation"]["severity"]] + 1
                    instanceLevelCount["Total"] = instanceLevelCount["Total"] + 1
            else:
                logging.debug(f"Version {version['versionName']} didn't have any vulnerable components.")
            versionLevelCounts["vulnerableComponentCountsByRemediationStatus"] = vulnerableComponentCountsByRemediationStatus
            projectVersionsCounts.append(versionLevelCounts)
        projectLevelCount["projectVersionLevelCounts"] = projectVersionsCounts

def isDormant(scanninDate):
    if scanninDate:
        findingDatetime = datetime.strptime(scanninDate, "%Y-%m-%dT%H:%M:%S.%fZ")
        comparedatetime = findingDatetime
        return True if (datetime.now()-comparedatetime).days > args.sinceDays else False
    else:
        return False
    
def getDate(source, whichDate):
    datetime_to_modify = None
    if whichDate in source and source[whichDate]:
       datetime_to_modify = datetime.strptime(source[whichDate], "%Y-%m-%dT%H:%M:%S.%fZ")
    if datetime_to_modify:
        return datetime.strftime(datetime_to_modify, "%B %d, %Y")
    return "-"

def addToTotals(projectCount, instanceLevelCount):
    remediationstatuses = ["NEW","IGNORED","DUPLICATE","MITIGATED","NEEDS_REVIEW","PATCHED","REMEDIATION_COMPLETE","REMEDIATION_REQUIRED", "NOT_AFFECTED"]
    severities = ["MEDIUM","HIGH","CRITICAL","LOW","Total"]
    for remediation in remediationstatuses:
        for severity in severities:
            instanceLevelCount[remediation][severity] = instanceLevelCount[remediation][severity] + projectCount[remediation][severity]
    instanceLevelCount["Total"] = instanceLevelCount["Total"] + projectCount["Total"]
    if "projectVersionCount" in projectCount:
        instanceLevelCount["ProjectTotalVersionCount"] = instanceLevelCount["ProjectTotalVersionCount"] + projectCount["projectVersionCount"]
    instanceLevelCount["SNIPPET"]["unreviewed"] = instanceLevelCount["SNIPPET"]["unreviewed"] + projectCount["SNIPPET"]["unreviewed"]
    instanceLevelCount["SNIPPET"]["reviewed"] = instanceLevelCount["SNIPPET"]["reviewed"] + projectCount["SNIPPET"]["reviewed"]
    instanceLevelCount["SNIPPET"]["ignored"] = instanceLevelCount["SNIPPET"]["ignored"] + projectCount["SNIPPET"]["ignored"]
    instanceLevelCount["SNIPPET"]["Total"] = instanceLevelCount["SNIPPET"]["Total"] + projectCount["SNIPPET"]["Total"]

def createPhaseFilterForVersions():
    phaseCategories = args.phaseCategories.split(',')
    phaseCategoryOptions = ""
    for phaseCategory in phaseCategories:
        phaseCategoryOptions += f'phase:{phaseCategory.strip().upper()},'
    return phaseCategoryOptions[:-1]

def createDistributionFilterForVersions():
    distributionCategories = args.distributionCategories.split(',')
    distributionCategoryOptions = ""
    for distributionCategory in distributionCategories:
        distributionCategoryOptions += f'distribution:{distributionCategory.strip().upper()},'
    return distributionCategoryOptions[:-1]

if __name__ == '__main__':
    try:
        start = timer()
        #Initialize the parser
        parser = argparse.ArgumentParser(
            description="Black Duck Metrics by Remediation Status."
        )
        #Parse commandline arguments
        parser.add_argument('--url', default=os.environ.get('BD_URL'), help="Baseurl for Black Duck Hub", required=False)
        parser.add_argument('--token', default=os.environ.get('BD_TOKEN'), help="BD Access token", required=False)
        parser.add_argument('--project', help="BD project name", required=False)
        parser.add_argument('--project_group_name', help="BD project group name", required=False)
        parser.add_argument('--version', help="BD project version name", required=False)
        parser.add_argument('--phaseCategories', help="Comma separated list of version phases, which will be selected. \
            Options are [PLANNING,DEVELOPMENT,RELEASED,DEPRECATED,ARCHIVED,PRERELEASE], default=\"PLANNING,DEVELOPMENT,RELEASED,DEPRECATED,ARCHIVED,PRERELEASE\"", default="PLANNING,DEVELOPMENT,RELEASED,DEPRECATED,ARCHIVED,PRERELEASE")
        parser.add_argument('--distributionCategories', help="Comma separated list of version distributions, which will be selected. \
            Options are [EXTERNAL,SAAS,INTERNAL,OPENSOURCE], default=\"EXTERNAL,SAAS,INTERNAL,OPENSOURCE\"", default="EXTERNAL,SAAS,INTERNAL,OPENSOURCE")
        parser.add_argument('--log_level', help="Will print more info... default=INFO", default="INFO")
        parser.add_argument('--html', action='store_true', help='generate HTML report')
        parser.add_argument('--pdf', action='store_true', help='generate PDF report')
        parser.add_argument('--json', action='store_true', help='generate json report')
        parser.add_argument('--csv', action='store_true', help='generate csv report')
        parser.add_argument('--dir', default='.', help='output directory (default: current directory)')
        parser.add_argument('--db_file', default='bd_remediation_db.json', help='TinyDB database file.')
        parser.add_argument('--cache', action='store_true', help='use tinyDB as a cache')
        parser.add_argument('--cache_truncate', action='store_true', help='will clean the given cache file')
        parser.add_argument('--sinceDays', type=int, default=30, help="The number of days before which to find project version dormant. (Default 30 days)", required=False)
        args = parser.parse_args()
        #Initializing the logger
        logging.getLogger("requests").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("blackduck.HubRestApi").setLevel(logging.WARNING)
        logging.basicConfig(format='%(asctime)s:%(levelname)s:%(module)s: %(message)s', stream=sys.stderr, level=args.log_level)
        #Printing out the version number
        logging.info("Black Duck Triage Extractor version: " + __versionro__)
        if logging.getLogger().isEnabledFor(logging.DEBUG): logging.debug(f'Given params are: {args}')
        if not args.url:
            logging.error("Black Duck URL is not given. You need to give it with --url or as an BD_URL environment variable!")
            exit()
        if not args.token:
            logging.error("Black Duck Access Token is not given. You need to give it with --token or as an BD_TOKEN environment variable!")
            exit()
        #Removing / -mark from end of url, if it exists
        args.url = f'{args.url if not args.url.endswith("/") else args.url[:-1]}'
        #DB Initialization
        db_file = args.dir + '/' + args.db_file
        path = Path(db_file)
        db = TinyDB(path, access_mode="r+")
        db.default_table_name = "projects"
        if args.cache_truncate:
            db.truncate()
        totals = addFindings()
        db.close()
        if totals:
            logging.info(f"Total project count: {totals['ProjectTotalCount']}")
            logging.info(f"Total version count: {totals['ProjectTotalVersionCount']}")
            logging.info(f"Total vulnerabilities count: {totals['Total']}")
            logging.info(f"Total NEW vulnerabilities count: {totals['NEW']}")
            logging.info(f"Total IGNORED vulnerabilities count: {totals['IGNORED']}")
            logging.info(f"Total DUPLICATE vulnerabilities count: {totals['DUPLICATE']}")
            logging.info(f"Total MITIGATED vulnerabilities count: {totals['MITIGATED']}")
            logging.info(f"Total NEEDS_REVIEW vulnerabilities count: {totals['NEEDS_REVIEW']}")
            logging.info(f"Total PATCHED vulnerabilities count: {totals['PATCHED']}")
            logging.info(f"Total REMEDIATION_COMPLETE vulnerabilities count: {totals['REMEDIATION_COMPLETE']}")
            logging.info(f"Total REMEDIATION_REQUIRED vulnerabilities count: {totals['REMEDIATION_REQUIRED']}")
            logging.info(f"Total NOT_AFFECTED vulnerabilities count: {totals['NOT_AFFECTED']}")
            logging.info(f"Total SNIPPET count: {totals['SNIPPET']}")
            if int(totals['Total']) > 0:
                timeFilenameFormat = '%Y%m%d%H%M%S'
                timeFormat = '%Y-%m-%d %H:%M:%S'
                polarisTimeFormat = '%Y-%m-%dT%H:%M:%S.%fZ'
                timestamp = datetime.today().strftime(timeFilenameFormat)
                outputPrefix = 'triageReport_bd_' + timestamp
                if (args.html or args.pdf):
                    # Setup template stuff
                    logging.info("Creating template for HTML and PDF reports....")
                    templateLoader = jinja2.FileSystemLoader(searchpath=templatesDir)
                    templateEnv = jinja2.Environment(loader=templateLoader, autoescape=True)
                    template = templateEnv.get_template(templateFile)
                    logging.info("Done")

                    logging.info("Rendering the template for HTML and PDF reports....")
                    htmlText = template.render(bdURL = args.url,
                                            reportTime = datetime.today().strftime(timeFormat),
                                            phases = args.phaseCategories,
                                            distibutions = args.distributionCategories,
                                            projectGroup = args.project_group_name,
                                            sinceDays = args.sinceDays,
                                            totals = totals)
                    logging.info("Done")

                    if (args.html):
                        logging.info("Creating HTML report...")
                        file = args.dir + '/' + outputPrefix + '.html'
                        with open(args.dir + '/' + outputPrefix + '.html', "w", encoding='utf-8') as fh:
                            fh.write(htmlText)
                        logging.info("Done")
                    if (args.pdf):
                        logging.info("Creating PDF report...")
                        options = {'enable-local-file-access': None}
                        pdfkit.from_string(htmlText, args.dir + '/' + outputPrefix + '.pdf', options=options)
                if (args.json):
                    logging.info("Creating JSON report...")
                    file = args.dir + '/' + outputPrefix + '.json'
                    f = open(file, "w", encoding="utf8")
                    f.write(json.dumps(totals, indent=3))
                    f.close()
                    logging.info("Done")
                if args.csv:
                    logging.info("Creating CVS report...")
                    df = pd.json_normalize(totals)
                    df.to_csv(args.dir + '/' + outputPrefix + '.csv', index=False, encoding='utf-8')
            else:
                logging.info("No vulnerable components found!")
        end = timer()
        usedTime = end - start
        logging.info(f"Took: {usedTime} seconds.")
        if totals and totals['ProjectTotalCount'] > 0:
            logging.info(f'average time per project: {usedTime/totals["ProjectTotalCount"]} seconds.')
        logging.info("Done")
    except Exception as e:
        db.close()
        logging.exception(e)
        raise SystemError(e)