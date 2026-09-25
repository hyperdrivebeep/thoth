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
    return <section className="workspace-content"><Callout intent="danger" title="이 작업 화면을 표시하지 못했습니다" role="alert">
      <p>서버 응답과 표시 형식이 맞지 않을 수 있습니다. 성공·완료로 처리하지 않으며 정본 기록을 바꾸지 않았습니다. 위 탭이나 왼쪽 프로젝트에서 다른 기록을 확인할 수 있습니다.</p>
      <Button icon="refresh" onClick={async()=>{await this.props.onRetry?.();this.setState({failed:false});}}>서버 상태 다시 읽고 재시도</Button>
    </Callout></section>;
  }
}
