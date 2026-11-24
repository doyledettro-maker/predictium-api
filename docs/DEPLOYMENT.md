# Production deployment

Production runs on ECS Fargate in `us-east-2`.

- CodeBuild project: `predictium-api-ecr-build`
- Source: manual encrypted S3 archive, not a GitHub webhook
- Source bucket: `predictium-codebuild-source-042729137627-us-east-2`
- ECR repository: `predictium`
- ECS cluster: `predictium-cluster`
- ECS service: `predictium-api-service`
- Task-definition family: `predictium-api-task`
- Load balancer: `predictium-alb`
- CloudWatch log group: `/ecs/predictium-api-task`

`buildspec.yml` builds the archive and pushes `predictium:latest`. The deployer
then adds an immutable full-commit tag to the same digest, registers a new task
definition that changes only the container image, and updates the existing ECS
service to that revision. ECS does not deploy `:latest` automatically.

## Preconditions

1. The exact main commit is independently reviewed and approved for deployment.
2. The checkout is clean and `origin/main` resolves to the approved commit and
   tree.
3. AWS CLI credentials can read CodeBuild, ECR, ECS, ELB, CloudWatch Logs, and
   the private source bucket, and can perform the existing build and ECS roll.
4. Record the current task definition, running image digest, service deployment
   ID, and target health before changing anything. These are the rollback
   receipt.

Do not change CodeBuild, IAM, networking, the task-definition environment, or
service configuration during an application deployment.

## Build the exact commit

Create the source from Git, not from a working directory:

```bash
git fetch origin main
COMMIT="$(git rev-parse origin/main)"
git archive --format=zip --output="/tmp/predictium-api-${COMMIT}.zip" "$COMMIT"

aws s3 cp \
  "/tmp/predictium-api-${COMMIT}.zip" \
  "s3://predictium-codebuild-source-042729137627-us-east-2/deploy/predictium-api-${COMMIT}.zip" \
  --sse AES256 \
  --only-show-errors

aws codebuild start-build \
  --region us-east-2 \
  --project-name predictium-api-ecr-build \
  --source-type-override S3 \
  --source-location-override \
    "predictium-codebuild-source-042729137627-us-east-2/deploy/predictium-api-${COMMIT}.zip"
```

Wait for that build ID to reach `SUCCEEDED`. Confirm its source location names
the approved full commit. Stop on any other terminal state.

After success, resolve `predictium:latest` to its digest and add the immutable
commit tag to that same manifest. Confirm `latest` and `$COMMIT` resolve to the
same digest before rolling ECS.

```bash
IMAGE_MANIFEST="$(aws ecr batch-get-image \
  --region us-east-2 \
  --repository-name predictium \
  --image-ids imageTag=latest \
  --accepted-media-types application/vnd.docker.distribution.manifest.v2+json \
  --query 'images[0].imageManifest' \
  --output text)"

aws ecr put-image \
  --region us-east-2 \
  --repository-name predictium \
  --image-tag "$COMMIT" \
  --image-manifest "$IMAGE_MANIFEST"
```

## Roll ECS

Describe the current task definition and produce a registration input that is
identical except for container `predictium-api` using:

```text
042729137627.dkr.ecr.us-east-2.amazonaws.com/predictium:<full-commit>
```

Register the next `predictium-api-task` revision, then update only the existing
service's task definition:

```bash
CURRENT_TASK_DEFINITION="$(aws ecs describe-services \
  --region us-east-2 \
  --cluster predictium-cluster \
  --services predictium-api-service \
  --query 'services[0].taskDefinition' \
  --output text)"

IMAGE="042729137627.dkr.ecr.us-east-2.amazonaws.com/predictium:${COMMIT}"
TASK_INPUT="$(aws ecs describe-task-definition \
  --region us-east-2 \
  --task-definition "$CURRENT_TASK_DEFINITION" \
  --output json | jq -c --arg image "$IMAGE" '
    .taskDefinition
    | del(.taskDefinitionArn, .revision, .status, .requiresAttributes,
          .compatibilities, .registeredAt, .registeredBy)
    | .containerDefinitions |= map(
        if .name == "predictium-api" then .image = $image else . end
      )
  ')"

NEW_TASK_DEFINITION="$(aws ecs register-task-definition \
  --region us-east-2 \
  --cli-input-json "$TASK_INPUT" \
  --query 'taskDefinition.taskDefinitionArn' \
  --output text)"

aws ecs update-service \
  --region us-east-2 \
  --cluster predictium-cluster \
  --service predictium-api-service \
  --task-definition "$NEW_TASK_DEFINITION"

aws ecs wait services-stable \
  --region us-east-2 \
  --cluster predictium-cluster \
  --services predictium-api-service
```

Verify desired/running/pending is `1/1/0`, the deployment is `COMPLETED`, the
sole running task uses the expected immutable tag and digest, and the new ALB
target is healthy.

## Post-deploy verification

Run probes from a trusted local machine. Keep Cognito tokens and keys in memory
or protected local files; never print or paste them.

1. `GET /health` returns 200.
2. Authenticated game-detail responses for free and premium access contain no
   `vegas`, `odds`, `books`, or `source_book` key at any depth.
3. A refused legacy ID such as
   `/predictions/games/game_detail_BOS_at_CLE_2026-03-08_x` returns 404.
4. Authenticated `/predictions/latest` retains consensus fields and contains no
   `books` key at any depth.
5. In `/ecs/predictium-api-task` logs after the rollout, the refused ID has no
   `game_details/<refused-id>` S3-read path and empty `books` fields produce no
   warning.

Record the CodeBuild build ID, ECR digest, task-definition revision, ECS
deployment ID, and probe results in the deployment handoff.

## Rollback

If any build, rollout, target-health, response, or log check fails, immediately
update `predictium-api-service` back to the previously recorded task-definition
revision. That revision points to its immutable prior digest. Wait for service
stability, confirm the prior digest is the sole healthy running target, and post
both the failed check and rollback receipt. Do not attempt an infrastructure or
IAM repair during rollback.
