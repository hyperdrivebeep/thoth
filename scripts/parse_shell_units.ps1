# Unapplied N01 candidate. Parse source as data; never invoke parsed commands.
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$source = [Console]::In.ReadToEnd()
$units = [System.Collections.Generic.List[object]]::new()
$unknown = [System.Collections.Generic.List[string]]::new()

function Add-Unknown {
    param([object]$Node, [string]$Reason)
    $offset = if ($null -eq $Node) { 0 } else { $Node.Extent.StartOffset }
    $unknown.Add("${Reason}@${offset}")
}

function Add-Literal {
    param([object]$Node, [System.Collections.Generic.List[string]]$Arguments)
    if ($Node -is [System.Management.Automation.Language.StringConstantExpressionAst]) {
        $Arguments.Add([string]$Node.Value)
        return
    }
    if ($Node -is [System.Management.Automation.Language.ConstantExpressionAst] -and
        ($Node.Value -is [int] -or $Node.Value -is [long])) {
        $Arguments.Add([string]$Node.Value)
        return
    }
    Add-Unknown $Node 'NON_LITERAL_ARGUMENT'
}

function Add-Statement {
    param([object]$Node, [int]$Depth)
    if ($Depth -gt 32 -or $units.Count -gt 256) {
        Add-Unknown $Node 'SYNTAX_BUDGET_EXCEEDED'
        return
    }
    if ($null -ne $Node.PSObject.Properties['Background'] -and $Node.Background) {
        Add-Unknown $Node 'BACKGROUND_PIPELINE'
        return
    }
    if ($Node.GetType().Name -eq 'PipelineChainAst') {
        Add-Statement $Node.LhsPipelineChain ($Depth + 1)
        Add-Statement $Node.RhsPipeline ($Depth + 1)
        return
    }
    if ($Node -isnot [System.Management.Automation.Language.PipelineAst]) {
        Add-Unknown $Node 'UNSUPPORTED_STATEMENT'
        return
    }
    foreach ($command in $Node.PipelineElements) {
        if ($command -isnot [System.Management.Automation.Language.CommandAst]) {
            Add-Unknown $command 'UNSUPPORTED_PIPELINE_EXPRESSION'
            continue
        }
        if ([string]$command.InvocationOperator -notin @('Unknown', 'Ampersand')) {
            Add-Unknown $command 'UNSUPPORTED_INVOCATION_OPERATOR'
            continue
        }
        $arguments = [System.Collections.Generic.List[string]]::new()
        foreach ($element in $command.CommandElements) {
            if ($element -is [System.Management.Automation.Language.CommandParameterAst]) {
                $arguments.Add('-' + $element.ParameterName)
                if ($null -ne $element.Argument) { Add-Literal $element.Argument $arguments }
            }
            else { Add-Literal $element $arguments }
        }
        $redirects = [System.Collections.Generic.List[object]]::new()
        foreach ($redirect in $command.Redirections) {
            if ($redirect -is [System.Management.Automation.Language.MergingRedirectionAst]) {
                continue
            }
            if ($redirect -isnot [System.Management.Automation.Language.FileRedirectionAst]) {
                Add-Unknown $redirect 'UNSUPPORTED_REDIRECTION'
                continue
            }
            $targets = [System.Collections.Generic.List[string]]::new()
            Add-Literal $redirect.Location $targets
            if ($targets.Count -eq 1) {
                $redirects.Add(@{ path = $targets[0]; append = [bool]$redirect.Append })
            }
        }
        $units.Add(@{ argv = @($arguments.ToArray()); redirects = @($redirects.ToArray()) })
    }
}

try {
    if ($source.Length -gt 65536) { throw 'SOURCE_BUDGET_EXCEEDED' }
    $tokens = $null
    $errors = $null
    $parsed = [System.Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors)
    foreach ($errorItem in $errors) { $unknown.Add('PARSE_ERROR:' + $errorItem.ErrorId) }
    foreach ($blockName in @('ParamBlock', 'BeginBlock', 'ProcessBlock', 'DynamicParamBlock')) {
        if ($null -ne $parsed.$blockName) { Add-Unknown $parsed.$blockName 'UNSUPPORTED_SCRIPT_BLOCK' }
    }
    if ($null -ne $parsed.PSObject.Properties['CleanBlock'] -and $null -ne $parsed.CleanBlock) {
        Add-Unknown $parsed.CleanBlock 'UNSUPPORTED_CLEAN_BLOCK'
    }
    if ($parsed.UsingStatements.Count -gt 0 -or $parsed.Attributes.Count -gt 0 -or
        $null -ne $parsed.ScriptRequirements) {
        Add-Unknown $parsed 'UNSUPPORTED_SCRIPT_DIRECTIVE'
    }
    if ($null -ne $parsed.EndBlock) {
        if ($parsed.EndBlock.Traps.Count -gt 0) { Add-Unknown $parsed.EndBlock 'UNSUPPORTED_TRAP' }
        foreach ($statement in $parsed.EndBlock.Statements) { Add-Statement $statement 0 }
    }
    @{ schema_version = '1.0.0'; units = @($units.ToArray()); unknown = @($unknown.ToArray()) } |
        ConvertTo-Json -Depth 12 -Compress
}
catch {
    # Do not echo the input or exception text; commands may carry sensitive values.
    @{ schema_version = '1.0.0'; units = @(); unknown = @('PARSER_FAILED') } |
        ConvertTo-Json -Depth 12 -Compress
}
