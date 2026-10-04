// Optimized pipeline for Jenkins: the same ideas as .github/workflows/ci-optimized.yml.
//
//  - the virtualenv is cached under JENKINS_HOME, keyed by a hash of the requirements files;
//  - static checks, four duration-balanced test shards and the Docker build run in parallel;
//  - the Docker engine reuses its layer cache between builds;
//  - coverage from the shards is merged and gated at 85 %, then the image is deployed to a
//    local staging container and smoke-tested.
pipeline {
    agent any

    options {
        timestamps()
        timeout(time: 30, unit: 'MINUTES')
        disableConcurrentBuilds()
        buildDiscarder(logRotator(numToKeepStr: '20'))
    }

    environment {
        PIP_DISABLE_PIP_VERSION_CHECK = '1'
        CACHE_ROOT = "${env.JENKINS_HOME}/caches"
        IMAGE = "shoplite:jenkins-${env.BUILD_NUMBER}"
    }

    stages {
        stage('Python environment (cached)') {
            steps {
                sh '''
                    set -eu
                    key=$(cat requirements.txt requirements-dev.txt | sha256sum | cut -c1-16)
                    venv="$CACHE_ROOT/venv-$key"
                    if [ -x "$venv/bin/python" ]; then
                        echo "Virtualenv cache hit ($key)"
                    else
                        echo "Virtualenv cache miss ($key), installing dependencies"
                        mkdir -p "$CACHE_ROOT"
                        python3 -m venv "$venv"
                        "$venv/bin/pip" install --quiet -r requirements-dev.txt
                    fi
                    ln -sfn "$venv" .venv
                '''
            }
        }

        stage('Checks, tests and build in parallel') {
            parallel {
                stage('Static checks') {
                    steps {
                        sh '.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/bandit -r app -q'
                    }
                }
                stage('Tests 1/4') {
                    steps { sh 'jenkins/run-shard.sh 1 4' }
                }
                stage('Tests 2/4') {
                    steps { sh 'jenkins/run-shard.sh 2 4' }
                }
                stage('Tests 3/4') {
                    steps { sh 'jenkins/run-shard.sh 3 4' }
                }
                stage('Tests 4/4') {
                    steps { sh 'jenkins/run-shard.sh 4 4' }
                }
                stage('Docker build (layer cache)') {
                    steps {
                        sh 'docker build --tag "$IMAGE" --build-arg GIT_SHA="$GIT_COMMIT" --build-arg BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)" .'
                    }
                }
            }
        }

        stage('Test report') {
            steps {
                sh '''
                    .venv/bin/coverage combine .coverage.shard-*
                    .venv/bin/coverage xml -o coverage.xml
                    .venv/bin/coverage report --fail-under=85
                '''
            }
            post {
                always {
                    junit 'reports/junit-*.xml'
                    recordCoverage(tools: [[parser: 'COBERTURA', pattern: 'coverage.xml']])
                }
            }
        }

        stage('Deploy to local staging') {
            steps {
                sh 'jenkins/deploy-staging.sh "$IMAGE"'
            }
        }

        stage('Smoke test') {
            steps {
                sh 'SMOKE_BASE_URL=http://shoplite-staging:8000 SMOKE_EXPECTED_SHA="$GIT_COMMIT" .venv/bin/pytest -m smoke smoke -q -p no:cacheprovider'
            }
        }
    }
}
