import time

from bson.objectid import ObjectId
from girder.exceptions import RestException
from girder.models.file import File
from girder.models.folder import Folder
from girder.models.item import Item
from girder.models.upload import Upload
from girder.utility.progress import ProgressContext, setResponseTimeLimit
from girder_jobs.constants import JobStatus
from girder_jobs.models.job import Job

from .models import ImportTrackerCancelError


def moveFile(file, folder, user, assetstore, progress, job):
    # check if the move has been canceled
    job = Job().load(job['_id'], force=True)
    if job['status'] == JobStatus.CANCELED:
        raise ImportTrackerCancelError()

    message = f'Moving {folder["name"]}/{file["name"]}\n'
    job = Job().updateJob(job, log=f'{time.strftime("%Y-%m-%d %H:%M:%S")} - {message}')
    progress.update(message=message)

    setResponseTimeLimit(86400)
    return Upload().moveFileToAssetstore(file, user, assetstore, progress=progress)


def moveFolder(user, folder, assetstore, ignoreImported, progress):
    """
    Move a folder to a different assetstore.

    :param user: the user requesting the move.
    :param folder: the folder to move.
    :param assetstore: the target assetstore.
    :param ignoreImported: if True, do not move imported files.
    :param progress: a boolean specifying if prgress should be reported.
    """
    job = Job().createJob(
        title='Move folder "%s" to assetstore "%s"' % (
            folder['name'], assetstore['name']),
        type='folder_move', public=False, user=user,
    )
    job = Job().updateJob(job, '%s - Starting folder move "%s" to assetstore "%s" (%s)\n' % (
        time.strftime(
            '%Y-%m-%d %H:%M:%S'), folder['name'], assetstore['name'], assetstore['_id']
    ), status=JobStatus.RUNNING)

    result = None
    try:
        with ProgressContext(progress, user=user,
                             title='Moving folder "%s" (%s) to assetstore "%s" (%s)' % (
                                 folder['name'],
                                 folder['_id'],
                                 assetstore['name'],
                                 assetstore['_id'])) as ctx:
            try:
                result = _moveLeafFiles(
                    folder, user, assetstore, ignoreImported, ctx, job)

                Job().updateJob(job, '%s - Finished folder move.\n' % (
                    time.strftime('%Y-%m-%d %H:%M:%S'),
                ), status=JobStatus.SUCCESS)

            except ImportTrackerCancelError:
                return 'Job canceled'

    except Exception as exc:
        Job().updateJob(job, '%s - Failed with %s\n' % (
            time.strftime('%Y-%m-%d %H:%M:%S'),
            exc,
        ), status=JobStatus.ERROR)

    return result


def _moveLeafFiles(folder, user, assetstore, ignoreImported, progress, job):
    # check if the move has been canceled
    job = Job().load(job['_id'], force=True)
    if job['status'] == JobStatus.CANCELED:
        raise ImportTrackerCancelError()

    Folder().updateFolder(folder)

    # only move files that are not already in the assetstore
    query = {'assetstoreId': {'$ne': ObjectId(assetstore['_id'])}}

    # ignore imported files if desired
    if ignoreImported:
        query['imported'] = {'$ne': True}

    folder_item = Item().findOne({
        'folderId': folder['_id'],
    })
    if not folder_item:
        raise RestException('Folder %s has no item' % folder['_id'])

    child_folders = Folder().childFolders(folder, 'folder', user=user)
    child_items = Folder().childItems(folder, filters=query)

    # get all files attached to an object
    def getAttached(attachedToId):
        uploads = []
        for attached_file in File().find({'attachedToId': attachedToId, **query}):
            upload = moveFile(attached_file, folder, user, assetstore, progress, job)
            uploads.append(upload)
        return uploads

    # upload all files attached to the current folder
    uploads = getAttached(folder_item['_id'])

    for item in child_items:
        # upload all attached files for each item
        uploads += getAttached(item['_id'])

        for file in File().find({'itemId': ObjectId(item['_id']), **query}):
            upload = moveFile(file, folder, user, assetstore, progress, job)
            uploads.append(upload)

    for child_folder in child_folders:
        uploads += _moveLeafFiles(child_folder, user, assetstore,
                                  ignoreImported, progress, job)

    return uploads
