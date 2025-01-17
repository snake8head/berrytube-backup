"""
Downloads videos from the Berrytube chatlog to a target directory.

Requires youtube-dl as an installed Python module. https://youtube-dl.org/
"""

import argparse
import os
import pathlib
import re
import sys
import urllib.request
import youtube_dl

from ChatLogReader import ChatLogReader

class Video(object):
    episodeRegex = re.compile(r'^\dx\d\d$')

    def __init__(self, logLine):
        title, vidId, source = self.parseLogLine(logLine)
        self.title = title
        self.vidId = vidId
        self.source = source
        self.playCount = 1
        self.isAnEpisode = Video.episodeRegex.match(self.title)

    def parseLogLine(self, logLine):
        payload = logLine.decode('utf-8').strip().split("Now Playing:")[1]
        youtubeDelimiter = " ( https://youtu.be/"
        vimeoDelimiter = " ( https://vimeo.com/"
        if youtubeDelimiter in payload:
            title, vidId = payload.split(youtubeDelimiter)
            return title, vidId, 'yt'
        elif vimeoDelimiter in payload:
            title, vidId =  payload.split(vimeoDelimiter)
            return title, vidId, 'vimeo'
        else:
            raise ValueError("Unrecognized video site {}".format(payload))

    def incrementCount(self):
        self.playCount += 1


class Logger(object):
    def __init__(self):
        self.errors = []
        self.ydl = None

    def to_stdout(self, message, skip_eol=False, check_quiet=False):
        """Print message to stdout if not in quiet mode."""
        if not check_quiet or not self.ydl.params.get('quiet', False):
            message = self.ydl._bidi_workaround(message)
            terminator = ['\n', ''][skip_eol]
            output = message + terminator
            self.ydl._write_string(output, self.ydl._screen_file)

    def to_stderr(self, message):
        """Print message to stderr."""
        message = self.ydl._bidi_workaround(message)
        output = message + '\n'
        self.ydl._write_string(output, self.ydl._err_file)

    def debug(self, msg):
        skip_eol = ' ETA ' in msg
        self.to_stdout(msg, skip_eol, check_quiet=True)

    def warning(self, msg):
        self.to_stdout(msg)

    def error(self, msg):
        self.errors.append(msg)
        self.to_stderr(msg)


def parseArgs():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-t', '--target', metavar='<directory>', type=str, dest='targetDirectory', required=True,
            help='directory to put the downloaded videos.  Will be created if it does not exist.')
    parser.add_argument('-r', '--requiredPlays', metavar='<integer>', type=int, dest='requiredPlays', required=False,
            help='number of plays a single video needs to have to warrant backing up.')
    parser.add_argument('-y', '--yes', action="store_true", dest='noPrompt', 
            help="automatically say yes to the 'are you sure?' prompt")
    parser.add_argument( '--no-progress', action="store_true", dest='noProgress', 
            help="Do not print progress bar (useful for Jenkins)")
    return parser.parse_args()


def getVideosById():
    print("Parsing videos from chat logs...")
    videosById = {}
    errors = []
    logReader = ChatLogReader()
    for line in logReader.listAllVideoPlayLines():
        try:
            video = Video(line)
        except:
            errors.append(line)
            continue
        if video.vidId in videosById:
            videosById[video.vidId].incrementCount()
        else:
            videosById[video.vidId] = video
    if len(errors) > 0:
        print("Unable to parse {} chat log lines".format(len(errors)))
    print("done.")
    return videosById


def getAlreadyDownloadedVidIds(targetDirectory):
    if not os.path.isdir(targetDirectory):
        return []
    return [parseId(v) for v in os.listdir(targetDirectory)]


def parseId(vidTitle):
    idPartition = vidTitle.split(' - ')[-1]
    return idPartition[:idPartition.find('.')]


def readInUnavailableVideos():
    try:
        with open("unavailableVideos.txt") as f:
            return set([v.strip() for v in f.readlines()])
    except FileNotFoundError:
        return set()


def filterVideos(videosById, alreadyDownloadedIds, knownUnavailableIds, requiredPlays):
    def videoShouldBeDownloaded(v):
        return v.playCount >= requiredPlays \
               and v.vidId not in alreadyDownloadedIds \
               and v.vidId not in knownUnavailableIds \
               and not v.isAnEpisode

    print("Filtering out videos with fewer than {} plays.".format(requiredPlays))
    return [v for v in videosById.values() if videoShouldBeDownloaded(v)]


def performDownload(videosToDownload, targetDirectory, noProgress):
    try:
        pathlib.Path(targetDirectory).mkdir(parents=True)
    except FileExistsError:
        pass
    urls = []
    for video in videosToDownload:
        if video.source == 'yt':
            urls.append("https://www.youtube.com/watch?v={}".format(video.vidId))
        elif video.source == 'vimeo':
            urls.append("https://vimeo.com/{}".format(video.vidId))
    options =  {
        'ignoreerrors': True,
        'outtmpl': "{}%(title)s - %(id)s.%(ext)s".format(targetDirectory)
    }
    if noProgress:
        options['noprogress'] = True
    logger = Logger()
    with youtube_dl.YoutubeDL(options) as ydl:
        logger.ydl = ydl # done here to reuse the default logger's nifty screen logging
        ydl.params['logger'] = logger
        try:
            ydl.download(urls)
        except KeyboardInterrupt:
            pass # let the program finish writing its error log
    return logger


def processErrors(logger, videosById):
    def printError(error):
        vidId = error.split(': ')[1]
        try:
            title = videosById[vidId].title
            print("\t{} (https://www.youtube.com/watch?v={})".format(title, vidId))
        except UnicodeEncodeError:
            print("\t{} (https://www.youtube.com/watch?v={})".format(title, vidId).encode('utf-8'))
        except KeyError:
            print("\tUnrecognized key: {}".format(vidId))

    print("ERRORS OCCURRED WHILE DOWNLOADING.  Some videos may be unavailable:")
    print("UNAVAILABLE VIDEOS:")
    for error in logger.errors:
        if "This video is unavailable." in error \
        or "This video is no longer available" in error \
        or "Unable to download webpage" in error:
            printError(error)
    print("\n")
    print("COPYRIGHT BLOCKED VIDEOS:")
    for error in logger.errors:
        if "blocked it on copyright grounds" in error:
            printError(error)
    print("\n")
    print("REGION BLOCKED VIDEOS:")
    for error in logger.errors:
        if "not available in your country" in error:
            printError(error)
    print("\n")
    print("FULL ERROR LOG:")
    newlyUnavailable = set()
    for error in logger.errors:
        vidId = error.split(': ')[1]
        newlyUnavailable.add(vidId)
        try:
            title = videosById[vidId].title
        except KeyError:
            print("KeyError on {}".format(vidId), flush=True)
            continue
        try:
            print("\t{}".format(error))
            print("\t\t{} (https://www.youtube.com/watch?v={})".format(title, vidId))
        except UnicodeEncodeError:
            print("\t{}".format(error).encode('utf-8'))
            print("\t\t{} (https://www.youtube.com/watch?v={})".format(title, vidId).encode('utf-8'))
    return newlyUnavailable


def main():
    targetDirectory = "V:/Media/berrytubeBackup/"
    requiredPlays = 5

    args = parseArgs()
    if args.targetDirectory is not None:
        targetDirectory = args.targetDirectory
    if args.requiredPlays is not None:
        requiredPlays = args.requiredPlays

    if not targetDirectory.endswith('/'):
        targetDirectory += '/'

    videosById = getVideosById()
    print("Found {} unique videos in the chat logs.".format(len(videosById)))

    alreadyDownloadedIds = getAlreadyDownloadedVidIds(targetDirectory)
    if len(alreadyDownloadedIds) > 0:
        print("Found {} videos already in target directory.".format(len(alreadyDownloadedIds)))

    knownUnavailableIds = readInUnavailableVideos()
    if len(knownUnavailableIds) > 0:
        print("Found {} known unavailable videos.".format(len(knownUnavailableIds)))

    videosToDownload = filterVideos(videosById, alreadyDownloadedIds, knownUnavailableIds, requiredPlays)
    if len(videosToDownload) == 0:
        print("No videos need to be downloaded.")
        return
    print("Will download {} videos to {}".format(len(videosToDownload), targetDirectory))

    if not args.noPrompt:
        answer = input("Do you want to continue? (yes/no)")
        if not (answer == 'y' or answer == 'yes'):
            return
    logger = performDownload(videosToDownload, targetDirectory, args.noProgress)
    if len(logger.errors) > 0:
        knownUnavailableIds.update(processErrors(logger, videosById))

    with open('unavailableVideos.txt', 'w') as unavailable:
        for vidId in knownUnavailableIds:
            print(vidId, file=unavailable)


if __name__ == "__main__":
    main()
