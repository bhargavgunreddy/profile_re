import React, { Component } from 'react'
import PropTypes from 'prop-types'
import './rating.scss'

const svgStarPoints = "9.9, 1.1, 3.3, 21.78, 19.8, 8.58, 0, 8.58, 16.5, 21.78";

interface RatingState{
    rating: number;
    ratingSet: boolean
}

interface RatingsProps{
}

export default class Rating extends Component<RatingsProps, RatingState> {
    static propTypes = {
        prop: PropTypes
    }
    inputRef: any

    constructor(props:any) {
        super(props)
        this.state = {
            rating: 0,
            ratingSet: false
        }
        
        this.inputRef = React.createRef();
    }

    handleClick = (event: any):void =>{
        if(event.target){
            let currentFill = event.target.firstElementChild.classList;
            if(currentFill.contains("full")){
                currentFill.replace("full","empty");
                this.setState({ratingSet: false});
            }else{
                currentFill.replace("empty","full");
                this.setState({
                    ratingSet: true,
                });
            }
        }
    }

    handleFocus = (event: any):void =>{
        let parentRef:any = event.target.parentElement;
        while(parentRef && !this.state.ratingSet){
            parentRef.firstElementChild?.classList.replace("empty", "full");
                parentRef = parentRef.nextSibling;
                let updatedRating = this.state.rating + 1
                this.setState({
                    rating: updatedRating
                });
        }
    }

    handleBlur = (event: any):void =>{
        let parentRef:any = event.target.parentElement;
        while(parentRef&& !this.state.ratingSet){
            parentRef.firstElementChild?.classList.replace("full", "empty");
                parentRef = parentRef.nextSibling;
                let updatedRating = this.state.rating - 1
                this.setState({
                    rating: updatedRating
                });
        }
    }

    render() {
        return (
            <React.Fragment>
            <div className="rating">
                
                <svg height="40" width="40" className="svg-icon" onClick={this.handleClick}>
                    <polygon id = "one" className="star empty" 
                    onMouseOver={this.handleFocus} onMouseOut={this.handleBlur}
                    points={svgStarPoints}/>
                </svg>
                <svg height="40" width="40" className="svg-icon" onClick={this.handleClick}>  
                    <polygon id = "two" className="star empty"  
                    onMouseOver={this.handleFocus} onMouseOut={this.handleBlur}
                    points={svgStarPoints}/>
                </svg>   
                <svg height="40" width="40" className="svg-icon" onClick={this.handleClick}>
                    <polygon id = "three" className="star empty" 
                onMouseOver={this.handleFocus} onMouseOut={this.handleBlur}
                points={svgStarPoints}
                 /></svg>
                <svg height="40" width="40" className="svg-icon" onClick={this.handleClick}>
                    <polygon id = "four" className="star empty"  
                    onMouseOver={this.handleFocus} onMouseOut={this.handleBlur}
                    points={svgStarPoints}
                 /></svg>
                <svg height="40" width="40" className="svg-icon" onClick={this.handleClick}>
                    <polygon id = "five" className="star empty"  
                    onMouseOver={this.handleFocus} onMouseOut={this.handleBlur}
                    points={svgStarPoints}/>
                </svg>
            </div>
            <div>
                    Rating : {this.state.rating}
            </div>
            </React.Fragment>
        )
    }
}
