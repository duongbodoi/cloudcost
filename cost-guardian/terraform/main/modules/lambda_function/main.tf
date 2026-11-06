# 1. Nén code: lambda_function.py + các file dùng chung -> một file zip
data "archive_file" "zip" {
  type        = "zip"
  output_path = "${path.root}/.build/${var.function_name}.zip"

  source {
    content  = file(var.source_file)
    filename = "lambda_function.py"
  }

  dynamic "source" {
    for_each = var.common_files
    content {
      content  = file(source.value)
      filename = basename(source.value)
    }
  }
}

# 2. IAM role: "ai được đeo role này" (trust policy) -> dịch vụ Lambda
data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "this" {
  name               = "${var.function_name}-role"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

# Quyền ghi log ra CloudWatch (policy có sẵn của AWS)
resource "aws_iam_role_policy_attachment" "logs" {
  role       = aws_iam_role.this.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Quyền riêng của luồng (SNS, Describe, Stop...) – chỉ tạo khi có truyền vào
resource "aws_iam_role_policy" "this" {
  count  = var.policy_json == null ? 0 : 1
  name   = "${var.function_name}-policy"
  role   = aws_iam_role.this.id
  policy = var.policy_json
}

# 3. Log group tạo trước, để đặt được thời gian giữ log
resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/lambda/${var.function_name}"
  retention_in_days = var.log_retention_days
}

# 4. Lambda
resource "aws_lambda_function" "this" {
  function_name    = var.function_name
  role             = aws_iam_role.this.arn
  handler          = var.handler
  runtime          = var.runtime
  filename         = data.archive_file.zip.output_path
  source_code_hash = data.archive_file.zip.output_base64sha256
  timeout          = var.timeout
  memory_size      = var.memory_size

  dynamic "environment" {
    for_each = length(var.environment) > 0 ? [1] : []
    content {
      variables = var.environment
    }
  }

  depends_on = [aws_cloudwatch_log_group.this, aws_iam_role_policy_attachment.logs]
}