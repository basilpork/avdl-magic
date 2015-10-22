var ytdlApp = angular.module('ytdlApp', []);

ytdlApp.controller('ytdlController',
    ['$scope', '$log', '$http', '$timeout',
    function($scope, $log, $http, $timeout) {

    $scope.alerts = [];

    var clearAlertsFrom = function(creator) {
        $scope.alerts = $scope.alerts.filter(function(element, index, array) {
            return element.creator != creator;
        });
    };

    $scope.enqueue = function() {
        clearAlertsFrom('enqueue');
        var yturl = $scope.yturl;
        $scope.yturl = '';
        $log.log('[Enqueue] Requesting ' + yturl);
        $http.post('/api/enqueue', {'yturl': yturl}).then(
            function success(r) {
                $log.log("[Enqueue] => " + r.status + " " + r.statusText + ". New job ID: " + r.data);
                watcher();
            }, function error(r) {
                $log.log("[Enqueue] => " + r.status + " " + r.statusText + ". Error: " + r.data.error);
                alertObject = {style:'danger', text:r.data.error, creator:'enqueue'};
                $scope.alerts.push(alertObject);

                if('info' in r.data) {
                    alertObject = {style:'info', text:r.data.info, creator:'enqueue'};
                    $scope.alerts.push(alertObject);
                }
            });
    };

    var anythingActive = function(jobs) {
        for(var i = 0; i < jobs.length; i++) {
            // RQ job states: ['queued', 'started', 'finished', 'failed']
            if(jobs[i].status === "started" || jobs[i].status === "queued") {
                return true;
            }
        }
        return false;
    };

    var mapLabels = {
        "queued": "info",
        "started": "primary",
        "finished": "success",
        "failed": "danger",
        "deleted": "default" // Defined by YTDL, not an RQ job state
    };
    var finishingTouches = function(jobs) {
        for(var i = 0; i < jobs.length; i++) {
            jobs[i].label = mapLabels[jobs[i].status];
        }
    };

    var watcher_instance = false;
    var watcher = function() {
        if(watcher_instance == true) {
            $log.log('[Watcher] Instance already running, aborting');
            return;
        } else {
            watcher_instance = true;
        }
        $http.get('/api/status').
            success(function(data) {
                $scope.jobs = data.jobs
                $scope.downloaded = data.files
                finishingTouches($scope.jobs);
                if(anythingActive($scope.jobs)) {
                    watcher_instance = false
                    $timeout(watcher, 1000); // milliseconds
                } else {
                    watcher_instance = false;
                    $log.log('[Watcher] No active jobs, ceasing');
                }
            }).
            error(function(error) {
                watcher_instance = false;
                $log.log(error);
            });
    };

    // Initialize queue & download status
    watcher();

}]);

