import { t } from "../i18n/locale";
import { Component, type ReactNode } from "react";
import { Button, Callout } from "@blueprintjs/core";

/** A rendering failure must not erase the project/navigation control plane. */
export class WorkspaceErrorBoundary extends Component<{children:ReactNode;resetKey:string;onRetry?:()=>Promise<unknown>},{failed:boolean}> {
  state={failed:false};
  static getDerivedStateFromError(){return {failed:true};}
  componentDidUpdate(previous:Readonly<{resetKey:string}>){
    if(this.state.failed && previous.resetKey!==this.props.resetKey)this.setState({failed:false});
  }
  render(){
    if(!this.state.failed)return this.props.children;
    return <section className="workspace-content"><Callout intent="danger" title={t("workspaceError.title")} role="alert">
      <p>{t("workspaceError.notice")}</p>
      <Button icon="refresh" onClick={async()=>{await this.props.onRetry?.();this.setState({failed:false});}}>{t("workspaceError.retry")}</Button>
    </Callout></section>;
  }
}
